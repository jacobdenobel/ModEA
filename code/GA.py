#!/usr/bin/env python
# -*- coding: utf-8 -*-

__author__ = 'Sander van Rijn <svr003@gmail.com>'

import numpy as np
import sys
from bbob import bbobbenchmarks, fgeneric
from code import getOpts, options, num_options
from code.Algorithms import customizedES, baseAlgorithm
from code.Individual import Individual
from code.Parameters import Parameters
import code.Selection as Sel
import code.Recombination as Rec


# BBOB parameters: Sets of noise-free and noisy benchmarks
free_function_ids = bbobbenchmarks.nfreeIDs
noisy_function_ids = bbobbenchmarks.noisyIDs
datapath = "test_results/"  # Where to store results
# Options to be stored in the log file(s)
bbob_opts = {'algid': None,
             'comments': '<comments>',
             'inputformat': 'col'}  # 'row' or 'col'
# Shortcut dictionary to index benchmark functions by name
fitness_functions = {'sphere': free_function_ids[0], 'elipsoid': free_function_ids[1],
                     'rastrigin': free_function_ids[2], }



def sysPrint(string):
    """ Small function to take care of the 'overhead' of sys.stdout.write + flush """
    sys.stdout.write(string)
    sys.stdout.flush()


def mutateBitstring(individual):
    """ Extremely simple 1/n bit-flip mutation """
    bitstring = individual.dna
    n = len(bitstring)
    p = 1/n
    for i in range(n):
        if np.random.random() < p:
            bitstring[i] = 1-bitstring[i]


def mutateIntList(individual, num_options):
    """ extremely simple 1/n random integer mutation """
    int_list = individual.dna
    n = len(int_list)
    p = 1/n
    for i in range(n):
        if np.random.random() < p:
            # -1 as random_integers is [1, val], -1 to simulate leaving out the current value
            new_int = np.random.random_integers(num_options[i]-1)-1
            if int_list[i] == new_int:
                new_int = num_options[i] - 1  # If we randomly selected the same value, pick the value we left out

            int_list[i] = new_int


def GA(n=10, budget=100, fitness_function='sphere'):
    """ Defines a Genetic Algorithm (GA) that evolves an Evolution Strategy (ES) for a given fitness function """

    # Fitness function to be passed on to the baseAlgorithm
    def fitnessFunction(x):
        return evaluate_ES(x, fitness_function)

    parameters = Parameters(n, budget, 1, 3)
    # Initialize the first individual in the population
    population = [Individual(n)]
    # TODO: rewrite to generic randint() version depending on len(options[i])
    population[0].dna = np.random.randint(2, size=len(options))
    population[0].fitness = fitnessFunction(population[0].dna)[0]

    # We use lambda functions here to 'hide' the additional passing of parameters that are algorithm specific
    functions = {
        'recombine': lambda pop: Rec.onePlusOne(pop),  # simply copy the only existing individual and return as a list
        'mutate': lambda ind: mutateIntList(ind, num_options),
        'select': lambda pop, new_pop, _: Sel.best(pop, new_pop, parameters),
        'mutateParameters': lambda t: parameters.oneFifthRule(t),
    }

    return baseAlgorithm(population, fitnessFunction, budget, functions, parameters)


def evaluate_ES(bitstring, fitness_function='sphere'):
    """ Single function to run all desired combinations of algorithms * fitness functions """

    # Set parameters
    n = 10
    budget = 500
    num_runs = 15

    # Setup the bbob logger
    bbob_opts['algid'] = bitstring
    f = fgeneric.LoggingFunction(datapath, **bbob_opts)

    print(bitstring, end=' ')
    opts = getOpts(bitstring)
    # define local function of the algorithm to be used, fixing certain parameters
    def algorithm(n, evalfun, budget):
        return customizedES(n, evalfun, budget, opts=opts)

    # '''
    # Actually running the algorithm is encapsulated in a try-except for now... math errors
    try:
        # Run the actual ES for <num_runs> times
        _, fitnesses = runAlgorithm(fitness_function, algorithm, n, num_runs, f, budget, opts)

        # From all different runs, retrieve the median fitness to be used as fitness for this ES
        min_fitnesses = np.min(fitnesses, axis=0)
        median = np.median(min_fitnesses)
        print("\t\t{}".format(median))

        # mean_best_fitness = np.mean(min_fitnesses)
        # print(" {}  \t({})".format(mean_best_fitness, median))
    # '''

    # _, fitnesses = runAlgorithm(fitness_function, algorithm, n, num_runs, f, budget, opts)
    #
    # # From all different runs, retrieve the median fitness to be used as fitness for this ES
    # min_fitnesses = np.min(fitnesses, axis=0)
    # median = np.median(min_fitnesses)
    # print("\t\t{}".format(median))


    # '''
    except Exception as e:
        # Give this ES fitness INF in case of runtime errors
        print(" np.inf: {}".format(e))
        # mean_best_fitness = np.inf
        median = np.inf
    # '''
    return [median]


def fetchResults(fun_id, instance, n, budget, opts):
    """ Small overhead-function to enable multi-processing """
    f = fgeneric.LoggingFunction(datapath, **bbob_opts)
    f_target = f.setfun(*bbobbenchmarks.instantiate(fun_id, iinstance=instance)).ftarget
    # Run the ES defined by opts once with the given budget
    results = customizedES(n, f.evalfun, budget, opts=opts)
    return f_target, results


def runAlgorithm(fit_name, algorithm, n, num_runs, f, budget, opts):

    fun_id = fitness_functions[fit_name]

    # Perform the actual run of the algorithm
    # '''  # Single-core version
    results = []
    targets = []
    for j in range(num_runs):
        # sysPrint('    Run: {}\r'.format(j))  # I want the actual carriage return here! No output clutter
        f_target = f.setfun(*bbobbenchmarks.instantiate(fun_id, iinstance=j)).ftarget
        targets.append(f_target)
        results.append(algorithm(n, f.evalfun, budget))

    '''  # Multi-core version ## TODO: Fix using dill/pathos/something else
    from multiprocessing import Pool
    p = Pool(4)
    function = lambda x: fetchResults(fun_id, x, n, budget, opts)
    run_data = p.map(function, range(num_runs))
    targets, results = zip(*run_data)
    #'''

    # Preprocess/unpack results
    _, sigmas, fitnesses, best_individual = (list(x) for x in zip(*results))
    sigmas = np.array(sigmas).T
    fitnesses = np.subtract(np.array(fitnesses).T, np.array(targets)[np.newaxis,:])

    return sigmas, fitnesses


def run():
    pass
    '''
    # Test all individual options
    n = len(options)
    evaluate_ES([0]*n)
    for i in range(n):
        for j in range(1, num_options[i]):
            dna = [0]*n
            dna[i] = j
            evaluate_ES(dna)

    # print(evaluate_ES([0,0,0,0,0,0,0,0]))
    # print(evaluate_ES([1,0,0,0,0,0,0,0]))
    # print(evaluate_ES([0,1,0,0,0,0,0,0]))
    # print(evaluate_ES([0,0,1,0,0,0,0,0]))
    # print(evaluate_ES([0,0,0,1,0,0,0,0]))
    # print(evaluate_ES([0,0,0,0,1,0,0,0]))
    # print(evaluate_ES([0,0,0,0,0,1,0,0]))
    # print(evaluate_ES([0,0,0,0,0,2,0,0]))
    # print(evaluate_ES([0,0,0,0,0,0,1,0]))
    # print(evaluate_ES([0,0,0,0,0,0,0,1]))

    print("\n\n")
    # '''

    '''
    # Known problems
    print("Combinations known to cause problems:")
    evaluate_ES([0,0,0,0,1,0,0,1])
    evaluate_ES([0,0,1,0,0,0,0,1])
    evaluate_ES([0,0,1,0,1,0,0,1])

    print("\n\n")
    # '''

    '''
    print("Mirrored vs Mirrored-pairwise")
    evaluate_ES([0,0,0,1,0,0,0,0])
    evaluate_ES([0,0,0,1,0,0,1,0])
    # '''


    # '''
    # Exhaustive/brute-force search over *all* possible combinations
    # NB: THIS ASSUMES OPTIONS ARE SORTED ASCENDING BY NUMBER OF VALUES
    print("Brute-force exhaustive search of *all* available ES-combinations.")
    print("Number of possible ES-combinations currently available: {}".format(np.product(num_options)))
    from collections import Counter
    from itertools import product
    from datetime import datetime, timedelta

    best_ES = None
    best_result = np.inf

    products = []
    # count how often there is a choice of x options
    counts = Counter(num_options)
    for num, count in sorted(counts.items(), key=lambda x: x[0]):
        products.append(product(range(num), repeat=count))

    x = datetime.now()
    for combo in product(*products):
        opts = list(sum(combo, ()))
        result = evaluate_ES(opts)[0]

        if result < best_result:
            best_result = result
            best_ES = opts

    y = datetime.now()


    print("Best ES found:       {}\n"
          "With median fitness: {}\n".format(best_ES, best_result))
    z = y - x
    days = z.days
    hours = z.seconds//3600
    minutes = (z.seconds % 3600) // 60
    seconds = (z.seconds % 60)

    print("Time at start:       {}\n"
          "Time at end:         {}\n"
          "Elapsed time:        {} days, {} hours, {} minutes, {} seconds".format(x, y, days, hours, minutes, seconds))
    # '''

    '''
    pop, sigmas, fitness, best = GA()
    print()
    print("Best Individual:     {}\n"
          "        Fitness:     {}\n"
          "Fitnesses over time: {}".format(best.dna, best.fitness, fitness))
    # '''

if __name__ == '__main__':
    np.set_printoptions(linewidth=200)
    # np.random.seed(42)
    run()
