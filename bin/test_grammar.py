try:
    import binutil  # required to import from dreamcoder modules
except ModuleNotFoundError:
    import bin.binutil  # alt import if called as module

from dreamcoder.grammar import *
from dreamcoder.domains.arithmetic.arithmeticPrimitives import *
from dreamcoder.domains.list.listPrimitives import *
from dreamcoder.program import Program
from dreamcoder.valueHead import *
from dreamcoder.zipper import *



bootstrapTarget()
g = Grammar.uniform([k0,k1,addition, subtraction, Program.parse('cons'), 
    Program.parse('car'), Program.parse('cdr'), Program.parse('empty'),
    Program.parse('fold')])
#g = g.randomWeights(lambda *a: random.random())
#p = Program.parse("(lambda (+ 1 $0))")
request = arrow(tint,tint)

def testEnumWithHoles():
    i = 0
    for ll,_,p in g.enumeration(Context.EMPTY,[],request,
                               6.,
                               enumerateHoles=True):

        i+=1
        if i==4:
            print("ref sketch", p)
            break

    for ll,_,pp in g.sketchEnumeration(Context.EMPTY,[],request, p,
                               6.,
                               enumerateHoles=True):
        print(pp)

        #ll_ = g.logLikelihood(request,p)
        #print(ll,p,ll_)
        #d = abs(ll - ll_)
        #assert d < 0.0001

def testSampleWithHoles():

    for _ in range(1000):
        p = g.sample(request, maximumDepth=6, maxAttempts=None, sampleHoleProb=.2)
        print(p, p.hasHoles)

        print("a sample from the sketch")
        print("\t", g.sampleFromSketch( request, p, sampleHoleProb=.2))

def findError():
    p = Program.parse('(lambda (fold <HOLE> 0 (lambda (lambda (+ $1 $0)) )))')
    for i in range(100):
        print("\t", g.sampleFromSketch( request, p, sampleHoleProb=.2))

def test_training_stuff():
    p = g.sample(request)
    print(p)

    for x in sketchesFromProgram(p, request, g):
        print(x)

def test_getTrace():
    full = g.sample(request)
    print("full:")
    print('\t', full)
    trace, negTrace= getTracesFromProg(full, request, g, onlyPos=False)
    print("trace:", *trace, sep='\n\t')
    #assert [full == p.betaNormalForm() trace]
    print("negTrace", *negTrace, sep='\n\t')

    assert trace[-1] == full
    return full


def test_sampleOneStep():
    for i in range(100):
        subtree = g._sampleOneStep(request, Context.EMPTY, [])
        print(subtree)

def test_holeFinder():

    #p = Program.parse('(lambda (fold <HOLE> 0 (lambda (lambda (+ $1 $0)) )))')
    for i in range(1):
        expr = g.sample( request, sampleHoleProb=.2)
        print("\t", expr )


    print([(subtree.path, subtree.env) for subtree in findHolesEnum(request, expr)])
    print([(subtree.path, subtree.env) for subtree in findHoles(expr, request)])

def test_sampleSingleStep():

    nxt = baseHoleOfType(request)
    zippers = findHoles(nxt, request)
    print(nxt)

    while zippers:
        nxt, zippers = sampleSingleStep(g, nxt, request, holeZippers=zippers)
        print(nxt)





# request = arrow(tint, tint)
# i = 0
# for ll,_,p in g.enumeration(Context.EMPTY,[],request,
#                            12.,
#                            enumerateHoles=False):


#     print(i, p)
#     #if i==7: break
#     i+=1

if __name__=='__main__':
    #findError()
    #testSampleWithHoles()
    #test_training_stuff()
    #test_holeFinder()
    #full = test_getTrace()
    #test_sampleOneStep()
    test_sampleSingleStep()
