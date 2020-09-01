try:
    import binutil  # required to import from dreamcoder modules
except ModuleNotFoundError:
    import bin.binutil  # alt import if called as module

import sys
sys.path.append('/afs/csail.mit.edu/u/m/mlbowers/clones') # needed for tmux sudo workaround


import argparse
from dreamcoder.grammar import *
from dreamcoder.domains.arithmetic.arithmeticPrimitives import *
from dreamcoder.domains.list.listPrimitives import *
from dreamcoder.program import Program
from dreamcoder.valueHead import *
from dreamcoder.zipper import *
from dreamcoder.SMC import SearchResult

from dreamcoder.domains.tower.towerPrimitives import *
import itertools
import torch

from dreamcoder.domains.list.makeDeepcoderData import DeepcoderTaskloader
from dreamcoder.domains.list.main import ListFeatureExtractor
from dreamcoder.domains.misc.deepcoderPrimitives import deepcoderPrimitives
from dreamcoder.valueHead import SimpleRNNValueHead, ListREPLValueHead, BaseValueHead, SampleDummyValueHead
from dreamcoder.policyHead import RNNPolicyHead,BasePolicyHead,ListREPLPolicyHead, NeuralPolicyHead
from dreamcoder.Astar import Astar
from likelihoodModel import AllOrNothingLikelihoodModel
from torch.utils.tensorboard import SummaryWriter
import mlb
import time
import matplotlib.pyplot as plot

class FakeRecognitionModel(nn.Module):
    # pretends to be whatever Astar wants from its RecognitionModel. Which isn't much lol
    def __init__(self,valueHead,policyHead):
        super().__init__()
        self.policyHead = policyHead
        self.valueHead = valueHead
    # def save(self, path):
    #     torch.save(self.state_dict(),path)
    # @staticmethod
    # def load(path):
    #     return torch.load

class Poisoned: pass
class State:
    def __init__(self):
        self.as_kwargs = self.__dict__
        self.state = self
        self.no_pickle = []
    def new(
            self,
            T=2,
            train='repl_rnn',
            test='repl_rnn',
            repeat=True,
            freeze_examples=False,
            print_every=200,
            H=64,
            test_every=2000,
            save_every=2000,
            batch_size=1000,
            num_tasks=None,
            value=False,
            policy=True,
            ):

        assert value or policy

        #w.add_image('colorful boi', torch.rand(3, 20, 20), dataformats='CHW')

        taskloader = DeepcoderTaskloader(
            f'dreamcoder/domains/list/DeepCoder_data/T{T}_A2_V512_L10_train_perm.txt',
            allowed_requests=[arrow(tlist(tint),tlist(tint))],
            repeat=True,
            num_tasks=num_tasks,
            )
        testloader = DeepcoderTaskloader(
            f'dreamcoder/domains/list/DeepCoder_data/T{T}_A2_V512_L10_test_perm.txt',
            allowed_requests=[arrow(tlist(tint),tlist(tint))],
            repeat=False,
            num_tasks=None,
            )

        extractor = ExtractorGenerator(H=H, maximumLength = taskloader.L+2)

        test_tasks = testloader.getTasks(100, ignore_eof=True)
        print(f'Got {len(test_tasks)} testing tasks')
        test_tasks_mini = test_tasks[:20]

        g = Grammar.uniform(deepcoderPrimitives())

        rnn_ph = RNNPolicyHead(g, extractor(0), H=H, encodeTargetHole=False, canonicalOrdering=True) if policy else None
        #pHead = BasePolicyHead()
        if value == 'tied':
            rnn_vh = rnn_ph.RNNHead
        else:
            rnn_vh = SimpleRNNValueHead(g, extractor(0), H=H) if value else SampleDummyValueHead()

        repl_ph = ListREPLPolicyHead(g, extractor(1), H=H, encodeTargetHole=False, canonicalOrdering=True)
        if value == 'tied':
            repl_vh = repl_ph.RNNHead
        else:
            repl_vh = ListREPLValueHead(g, extractor(1), H=H) if value else SampleDummyValueHead()
        
        if 'rnn' not in train:
            rnn_vh = rnn_ph = None
        if 'repl' not in train:
            repl_vh = repl_ph = None

        
        heads = list(filter(None,[repl_ph, repl_vh, rnn_ph, rnn_vh]))
        vheads = list(filter(lambda h: isinstance(h,BaseValueHead),heads))
        pheads = list(filter(lambda h: isinstance(h,NeuralPolicyHead),heads))

        max_depth = 10

        params = itertools.chain.from_iterable([head.parameters() for head in heads])
        optimizer = torch.optim.Adam(params, lr=0.001, eps=1e-3, amsgrad=True)

        rnn_astar = Astar(FakeRecognitionModel(rnn_vh, rnn_ph), maxDepth=max_depth)
        repl_astar = Astar(FakeRecognitionModel(repl_vh, repl_ph), maxDepth=max_depth)
        astars = []
        if 'rnn' in test:
            assert 'rnn' in train
            astars.append(rnn_astar)
        if 'repl' in test:
            assert 'repl' in train
            astars.append(repl_astar)

        j=0
        frontiers = None
        self.update(locals()) # do self.* = * for everything
        self.post_load()
    
    def save(self, locs, path):
        """
        use like state.save(locals(),"path/to/file")
        """
        temp = {}
        for key in self.no_pickle:
            temp[key] = self[key]
            self[key] = Poisoned
        torch.save(self, f'experimentOutputs/{path}')
        for key in self.no_pickle:
            self[key] = temp[key]
    def load(self, path):
        state = torch.load(f'experimentOutputs/{path}')
        self.update(state.__dict__)
        self.post_load()
    def post_load(self):
        print("intializing tensorboard")
        w = SummaryWriter(
            log_dir='runs/test',
            max_queue=10,
        )
        print("done")
        self.no_pickle.append('w')
        self.update(locals())
        

    def __getitem__(self,key):
        return getattr(self,key)
    def __setitem__(self,key,val):
        return setattr(self,key,val)
    def __repr__(self):
        body = []
        for k,v in self.__dict__.items():
            body.append(f'{k}: {repr(v)}')
        body = '\n\t'.join(body)
        return f"State(\n\t{body}\n)"
    def update(self,dict):
        for k,v in dict.items():
            self[k] = v

def train_model(
    state,
    freeze_examples,
    taskloader,
    vheads,
    pheads,
    heads,
    batch_size,
    print_every,
    save_every,
    w,
    g,
    optimizer,
    test_every,
    astars,
    test_tasks_mini,
    test_tasks,
    frontiers=None,
    j=0,
    **kwargs,
        ):
    tstart = time.time()
    while True:
        # TODO you should really rename getTask to getProgramAndTask or something
        if frontiers is None or not freeze_examples:
            prgms_and_tasks = [taskloader.getTask() for _ in range(batch_size)]
            tasks = [task for program,task in prgms_and_tasks]
            frontiers = [FakeFrontier(program,task) for program,task in prgms_and_tasks]
        for f in frontiers: # work thru batch of `batch_size` examples
            for head in heads:
                head.zero_grad()
            # TODO TEMP
            losses = []
            for head in vheads:
                loss = head.valueLossFromFrontier(f, g)
                losses.append(loss)
            for head in pheads:
                loss = head.policyLossFromFrontier(f, g)
                losses.append(loss)
            
            sum(losses).backward()
            optimizer.step()

            # printing and logging
            if j % print_every == 0:
                for head,loss in zip(vheads+pheads,losses): # important that the right things zip together (both lists ordered same way)
                    print(f"[{j}] {head.__class__.__name__} {loss.item()}")
                    w.add_scalar(head.__class__.__name__, loss.item(), j)
                print()
                w.flush()

            if j % save_every == 0:
                print("saving...")
                state.save(locals(),'test_save')
                print("done")

            # testing
            if test_every is not None and j % test_every == 0:
                if j != 0:
                    elapsed = time.time()-tstart
                    print(f"{test_every} steps in {elapsed:.1f}s ({test_every/elapsed:.1f} steps/sec)")
                model_results = test_models(astars, test_tasks_mini, timeout=3, verbose=True)
                plot_model_results(model_results, file='mini_test', salt=j)
                tstart = time.time()
            j += 1
            def fn():
                model_results = test_models(astars,test_tasks,timeout=3, verbose=True)
                plot_model_results(model_results, file='plots')
            mlb.callback('test',fn)






    #def __getstate__(self):
        #Classes can further influence how their instances are pickled; if the class defines the method __getstate__(), it is called and the returned object is pickled as the contents for the instance, instead of the contents of the instance’s dictionary. If the __getstate__() method is absent, the instance’s __dict__ is pickled as usual.
    #def __setstate__(self,state):
        #Upon unpickling, if the class defines __setstate__(), it is called with the unpickled state. In that case, there is no requirement for the state object to be a dictionary. Otherwise, the pickled state must be a dictionary and its items are assigned to the new instance’s dictionary.
        #Note If __getstate__() returns a false value, the __setstate__() method will not be called upon unpickling.

class FakeFrontier:
    # pretends to be whatever valueLossFromFrontier wants for simplicity
    def __init__(self,program,task):
        self.task = task # satisfies frontier.task call
        self._fullProg = program
        self.program = self # trick for frontier.sample().program._fullProg
    def sample(self):
        return self

class ExtractorGenerator:
    def __init__(self,H,maximumLength):
        self.H = H
        self.maximumLength = maximumLength
        self._groups = {}
    def __call__(self, group):
        """
        Returns an extractor object. If called twice with the same group (an int or string or anything) the same object will be returned (ie share weights)
        """
        if group not in self._groups:
            self._groups[group] = ListFeatureExtractor(maximumLength=self.maximumLength, H=self.H, cuda=True)
        return self._groups[group]






# def test_trainListREPL(
#     T=1,
#     train='repl',
#     test='repl',
#     repeat=True,
#     freeze_examples=False,
#     print_every=200,
#     H=64,
#     test_every=2000,
#     save_every=10,
#     batch_size=1000,
#     num_tasks=None,
#     value=False,
#     policy=True,
#     ):
#     """
#     share_extractor means all value/policy heads will share the same LearnedFeatureExtractor which is often what you want unless
#     comparing multiple value functions side by side.

#     Test 1: a single super simple program and the training examples are all the same
#         T=1
#         freeze_examples=True
#         batch_size=1 # this is important
#         num_tasks=1
#         repeat=True
#         test_every=None
#     Test 2: same as test 1 but training examples change for that one program
#         T=1
#         freeze_examples=False
#         # (batch_size no longer matters)
#         num_tasks=1
#         repeat=True
#         test_every=None
#     Test 3:
#         num_tasks = 3
#     Test 4:
#         T=1
#         num_tasks = None
#         test_every = 1000


#     """
#     assert value or policy

#     print("intializing tensorboard")
#     w = SummaryWriter(
#         log_dir='runs/test',
#         max_queue=10,
#     )
#     print("done")
#     #w.add_image('colorful boi', torch.rand(3, 20, 20), dataformats='CHW')

#     taskloader = DeepcoderTaskloader(
#         f'dreamcoder/domains/list/DeepCoder_data/T{T}_A2_V512_L10_train_perm.txt',
#         allowed_requests=[arrow(tlist(tint),tlist(tint))],
#         repeat=True,
#         num_tasks=num_tasks,
#         )
#     testloader = DeepcoderTaskloader(
#         f'dreamcoder/domains/list/DeepCoder_data/T{T}_A2_V512_L10_test_perm.txt',
#         allowed_requests=[arrow(tlist(tint),tlist(tint))],
#         repeat=False,
#         num_tasks=None,
#         )

#     extractor = ExtractorGenerator(H=H, maximumLength = taskloader.L+2)

#     test_tasks = testloader.getTasks(100, ignore_eof=True)
#     test_tasks_mini = test_tasks[:10]

#     g = Grammar.uniform(deepcoderPrimitives())

#     rnn_ph = RNNPolicyHead(g, extractor(0), H=H, encodeTargetHole=False, canonicalOrdering=True) if policy else None
#     #pHead = BasePolicyHead()
#     if value == 'tied':
#         rnn_vh = rnn_ph.RNNHead
#     else:
#         rnn_vh = SimpleRNNValueHead(g, extractor(0), H=H) if value else SampleDummyValueHead()

#     repl_ph = ListREPLPolicyHead(g, extractor(1), H=H, encodeTargetHole=False, canonicalOrdering=True)
#     if value == 'tied':
#         repl_vh = repl_ph.RNNHead
#     else:
#         repl_vh = ListREPLValueHead(g, extractor(1), H=H) if value else SampleDummyValueHead()
    
#     if 'rnn' not in train:
#         rnn_vh = rnn_ph = None
#     if 'repl' not in train:
#         repl_vh = repl_ph = None

    
#     heads = list(filter(None,[repl_ph, repl_vh, rnn_ph, rnn_vh]))
#     vheads = list(filter(lambda h: isinstance(h,BaseValueHead),heads))
#     pheads = list(filter(lambda h: isinstance(h,NeuralPolicyHead),heads))

#     max_depth = 10

#     params = itertools.chain.from_iterable([head.parameters() for head in heads])
#     optimizer = torch.optim.Adam(params, lr=0.001, eps=1e-3, amsgrad=True)

#     rnn_astar = Astar(FakeRecognitionModel(rnn_vh, rnn_ph), maxDepth=max_depth)
#     repl_astar = Astar(FakeRecognitionModel(repl_vh, repl_ph), maxDepth=max_depth)
#     astars = []
#     if 'rnn' in test:
#         assert 'rnn' in train
#         astars.append(rnn_astar)
#     if 'repl' in test:
#         assert 'repl' in train
#         astars.append(repl_astar)

#     j=0
#     tstart = time.time()
#     frontiers = None
#     while True:
#         # TODO you should really rename getTask to getProgramAndTask or something
#         if frontiers is None or not freeze_examples:
#             prgms_and_tasks = [taskloader.getTask() for _ in range(batch_size)]
#             tasks = [task for program,task in prgms_and_tasks]
#             frontiers = [FakeFrontier(program,task) for program,task in prgms_and_tasks]
#         for f in frontiers: # work thru batch of `batch_size` examples
#             #mlb.green(f._fullProg)
#             for head in heads:
#                 head.zero_grad()
#             # TODO TEMP
#             losses = []
#             for head in vheads:
#                 loss = head.valueLossFromFrontier(f, g)
#                 losses.append(loss)
#             for head in pheads:
#                 loss = head.policyLossFromFrontier(f, g)
#                 losses.append(loss)
            
#             sum(losses).backward()
#             optimizer.step()

#             # printing and logging
#             if j % print_every == 0:
#                 for head,loss in zip(vheads+pheads,losses): # important that the right things zip together (both lists ordered same way)
#                     print(f"[{j}] {head.__class__.__name__} {loss.item()}")
#                     w.add_scalar(head.__class__.__name__, loss.item(), j)
#                 print()
#                 w.flush()
#             if j % save_every == 0:
#                 print("saving...")
#                 w.close()
#                 s.save_locals()
#                 torch.save(locals(),'experimentOutputs/testlocals')
#                 print("done")

#             # testing
#             if test_every is not None and j % test_every == 0:
#                 if j != 0:
#                     elapsed = time.time()-tstart
#                     print(f"{test_every} steps in {elapsed:.1f}s ({test_every/elapsed:.1f} steps/sec)")
#                 model_results = test_models(astars, test_tasks_mini, timeout=3, verbose=True)
#                 plot_model_results(model_results, file='experimentOutputs/test')
#                 tstart = time.time()
#             j += 1


def test_models(astars, test_tasks, timeout, verbose=True):
    model_results = []
    for astar in astars:
        name = f"{astar.owner.policyHead.__class__.__name__}_&&_{astar.owner.valueHead.__class__.__name__}"
        print(f"Testing: {name}")
        search_results = []
        likelihoodModel = AllOrNothingLikelihoodModel(timeout=0.01)
        for program, task in test_tasks:
            g = Grammar.uniform(deepcoderPrimitives())
            fs, times, num_progs, solns = astar.infer(
                    g, 
                    [task],
                    likelihoodModel, 
                    timeout=timeout,
                    elapsedTime=0,
                    evaluationTimeout=0.01,
                    maximumFrontiers={task: 2},
                    CPUs=1,
                ) 
            solns = solns[task]
            times = times[task]
            if len(solns) > 0:
                assert len(solns) == 1 # i think this is true, I want it to be true lol
                soln = solns[0]
                search_results.append(soln)
                if verbose: mlb.green(f"solved {task.name} with {len(solns)} solns in {times:.2f}s (searched {num_progs} programs)")
            else:
                if verbose: mlb.red(f"failed to solve {task.name} (searched {num_progs} programs)")
        model_results.append(ModelResult(name, search_results, len(test_tasks)))
        if verbose: mlb.blue(f'solved {len(search_results)}/{len(test_tasks)} tasks ({len(search_results)/len(test_tasks)*100:.1f}%)\n')
    return model_results

class ModelResult:
    def __init__(self, name, search_results, num_tests):
        self.empty = (len(search_results) == 0)
        if len(search_results) > 0:
            assert isinstance(search_results[0], SearchResult)
        self.search_results = search_results
        self.num_tests = num_tests
        self.name = name
        if not self.empty:
            self.max_time = max([r.time for r in search_results])
            self.max_evals = max([r.evaluations for r in search_results])
        else:
            self.max_time = 0
            self.max_evals = 0
    def fraction_hit(self, predicate):
        valid = [r for r in self.search_results if predicate(r)]
        return len(valid)/self.num_tests*100

def plot_model_results(model_results, file=None, salt=''):
    assert isinstance(model_results, list)
    assert isinstance(model_results[0], ModelResult)

    # plot vs time
    plot.figure()
    plot.xlabel('Time')
    plot.ylabel('percent correct')
    plot.ylim(bottom=0., top=100.)
    for model_result in model_results:
        xs = list(np.arange(0,model_result.max_time,0.1)) # start,stop,step
        plot.plot(xs,
                [model_result.fraction_hit(lambda r: r.time < x) for x in xs],
                label=model_result.name,
                linewidth=4)
    plot.legend()

    if file:
        plot.savefig(f"experimentOutputs/{file}_time.png")
        mlb.yellow(f"saved plot to experimentOutputs/{file}_time.png")
    else:
        plot.show()

    # plot vs evaluations
    plot.figure()
    plot.xlabel('Evaluations')
    plot.ylabel('percent correct')
    plot.ylim(bottom=0., top=100.)
    for model_result in model_results:
        xs = list(range(model_result.max_evals))
        plot.plot(xs,
                [model_result.fraction_hit(lambda r: r.evaluations <= x) for x in xs],
                label=model_result.name,
                linewidth=4)
    plot.legend()

    if file:
        plot.savefig(f"experimentOutputs/{file}_evals@{salt}.png")
        mlb.yellow(f"saved plot to experimentOutputs/{file}_evals@{salt}.png\n")
    else:
        plot.show()

if __name__ == '__main__':
    #parser = argparse.ArgumentParser(description='Test the List REPL')
    #parser.add_argument('integers', metavar='N', type=int, nargs='+',
    #                    help='an integer for the accumulator')
    # parser.add_argument('--train', nargs='+',
    #                     const=sum, default=max,
    #                     help='sum the integers (default: find the max)')



    with torch.cuda.device(6):
        s = State()
        s.new()
        train_model(**s.as_kwargs)
