#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Collection of some standard algorithms and the fully customizable CMA-ES as used in 'Evolving the Structure of
Evolution Strategies', all based on the same :func:`~baseAlgorithm` function.
"""
from __future__ import absolute_import, division, print_function, unicode_literals

__author__ = 'Sander van Rijn <svr003@gmail.com>'
# External libraries
import numpy as np
from copy import copy
from functools import partial
from numpy import ceil, floor, log, ones
# Internal classes
from .Individual import FloatIndividual
from .Parameters import Parameters
from code import Config
from code.Utils import options, num_options_per_module
# Internal modules
import code.Mutation as Mut
import code.Recombination as Rec
import code.Selection as Sel
import code.Sampling as Sam


# Example algorithms
def onePlusOneES(n, fitnessFunction, budget):
    """
        Implementation of the default (1+1)-ES
        Requires the length of the vector to be optimized, the handle of a fitness function to use and the budget

        :param n:               Dimensionality of the problem to be solved
        :param fitnessFunction: Function to determine the fitness of an individual
        :param budget:          Number of function evaluations allowed for this algorithm
        :returns:               The statistics generated by running the algorithm
    """

    parameters = Parameters(n, budget, 1, 1)
    population = [FloatIndividual(n)]

    # We use functions here to 'hide' the additional passing of parameters that are algorithm specific
    recombine = Rec.onePlusOne
    mutate = partial(Mut.addRandomOffset, sampler=Sam.GaussianSampling(n))
    select = Sel.onePlusOneSelection
    mutateParameters = parameters.oneFifthRule

    functions = {
        'recombine': recombine,
        'mutate': mutate,
        'select': select,
        'mutateParameters': mutateParameters,
    }

    _, results = baseAlgorithm(population, fitnessFunction, budget, functions, parameters)

    return results


def CMA_ES(n, fitnessFunction, budget, mu=None, lambda_=None, elitist=False):
    """
        Implementation of a default (mu +/, lambda)-CMA-ES
        Requires the length of the vector to be optimized, the handle of a fitness function to use and the budget

        :param n:               Dimensionality of the problem to be solved
        :param fitnessFunction: Function to determine the fitness of an individual
        :param budget:          Number of function evaluations allowed for this algorithm
        :param mu:              Number of individuals that form the parents of each generation
        :param lambda_:         Number of individuals in the offspring of each generation
        :param elitist:         Boolean switch on using a (mu, l) strategy rather than (mu + l). Default: False
        :returns:               The statistics generated by running the algorithm
    """

    parameters = Parameters(n, budget, mu, lambda_, elitist=elitist)
    population = [FloatIndividual(n) for _ in range(mu)]

    # Artificial init
    wcm = parameters.wcm
    for individual in population:
        individual.genotype = wcm

    # We use functions here to 'hide' the additional passing of parameters that are algorithm specific
    recombine = Rec.weighted
    mutate = partial(Mut.CMAMutation, sampler=Sam.GaussianSampling(n))
    def select(pop, new_pop, _, params):
        return Sel.best(pop, new_pop, params)
    mutateParameters = parameters.adaptCovarianceMatrix

    functions = {
        'recombine': recombine,
        'mutate': mutate,
        'select': select,
        'mutateParameters': mutateParameters,
    }

    _, results = baseAlgorithm(population, fitnessFunction, budget, functions, parameters)

    return results


# Evolving ES
class _CustomizedES(object):
    """
        This function accepts a dictionary of options 'opts' which selects from a large range of different
        functions and combinations of those. Instrumental in Evolving Evolution Strategies

        :param n:               Dimensionality of the problem to be solved
        :param budget:          Number of function evaluations allowed for this algorithm
        :param mu:              Number of individuals that form the parents of each generation
        :param lambda_:         Number of individuals in the offspring of each generation
        :param opts:            Dictionary containing the options (elitist, active, threshold, etc) to be used
        :param values:          Dictionary containing initial values for initializing (some of) the parameters
        :returns:               The statistics generated by running the algorithm
    """

    # TODO: make dynamically dependent
    bool_default_opts = ['active', 'elitist', 'mirrored', 'orthogonal', 'sequential', 'threshold', 'tpa']
    string_default_opts = ['base-sampler', 'ipop', 'selection', 'weights_option']

    def __init__(self, n, budget, mu=None, lambda_=None, opts=None, values=None):
        self.l_bound = ones((n, 1)) * -5
        self.u_bound = ones((n, 1)) * 5
        self.budget = budget
        self.n = n

        self.addDefaults(opts)

        if opts['selection'] == 'pairwise':
            selector = Sel.pairwise
        else:
            selector = Sel.best

        lambda_, eff_lambda, mu = self.calculateDependencies(opts, lambda_, mu)

        def select(pop, new_pop, _, param):
            return selector(pop, new_pop, param)
        self.select = select

        # Pick the lowest-level sampler
        if opts['base-sampler'] == 'quasi-sobol':
            sampler = Sam.QuasiGaussianSobolSampling(n)
        elif opts['base-sampler'] == 'quasi-halton' and Sam.halton_available:
            sampler = Sam.QuasiGaussianHaltonSampling(n)
        else:
            sampler = Sam.GaussianSampling(n)

        # Create an orthogonal sampler using the determined base_sampler
        if opts['orthogonal']:
            orth_lambda = eff_lambda
            if opts['mirrored']:
                orth_lambda = max(orth_lambda // 2, 1)
            sampler = Sam.OrthogonalSampling(n, lambda_=orth_lambda, base_sampler=sampler)

        # Create a mirrored sampler using the sampler (structure) chosen so far
        if opts['mirrored']:
            sampler = Sam.MirroredSampling(n, base_sampler=sampler)

        self.parameter_opts = {'n': n, 'budget': budget, 'mu': mu, 'lambda_': lambda_, 'u_bound': self.u_bound,
                               'l_bound': self.l_bound,
                               'weights_option': opts['weights_option'], 'active': opts['active'],
                               'elitist': opts['elitist'],
                               'sequential': opts['sequential'], 'tpa': opts['tpa'], 'local_restart': opts['ipop'],
                               'values': values,
                               }

        # In case of pairwise selection, sequential evaluation may only stop after 2mu instead of mu individuals
        self.mu_int = int(1 + floor(mu * (eff_lambda - 1)))
        if opts['sequential'] and opts['selection'] == 'pairwise':
            self.parameter_opts['seq_cutoff'] = 2

        # We use functions/partials here to 'hide' the additional passing of parameters that are algorithm specific
        self.recombine = Rec.weighted
        self.mutate = partial(Mut.CMAMutation, sampler=sampler, threshold_convergence=opts['threshold'])
        self.opts = opts

    # TODO: move function to fit with other opts-dictionary related stuff
    def addDefaults(self, opts):
        # Boolean defaults, if not given
        for op in self.bool_default_opts:
            if op not in opts:
                opts[op] = False

        # String defaults, if not given
        for op in self.string_default_opts:
            if op not in opts:
                opts[op] = None

    def calculateDependencies(self, opts, lambda_, mu):
        if lambda_ is None:
            lambda_ = int(4 + floor(3 * log(self.n)))
        eff_lambda = lambda_
        if mu is None:
            mu = 0.5

        if opts['tpa']:
            if lambda_ <= 4:
                lambda_ = 4
                eff_lambda = 2
            else:
                eff_lambda = lambda_ - 2

        if opts['selection'] == 'pairwise':
            # Explicitly force lambda_ to be even
            if lambda_ % 2 == 1:
                lambda_ -= 1
                if lambda_ == 0:  # If lambda_ is too low, make it be at least one pair
                    lambda_ += 2

            if opts['tpa']:
                if lambda_ == 2:
                    lambda_ += 2
                eff_lambda = lambda_ - 2
            else:
                eff_lambda = lambda_

            if mu >= 0.5:  # We cannot select more than half of the population when only half is actually available
                mu /= 2

        return lambda_, eff_lambda, mu

    def runOptimizer(self, fitnessFunction):
        """
            Performs the actual optimization run using the set parameters.

            :param fitnessFunction: Function to determine the fitness of an individual
            :returns:               The statistics generated by running the algorithm
        """
        functions = {
            'recombine': self.recombine,
            'mutate': self.mutate,
            'select': self.select,
        }

        if self.opts['ipop']:
            results = localRestartAlgorithm(fitnessFunction, self.budget, functions, self.parameter_opts)
        else:
            # Init all individuals of the first population at the same random point in the search space
            population = [FloatIndividual(self.n) for _ in range(self.mu_int)]
            wcm = (np.random.randn(self.n, 1) * (self.u_bound - self.l_bound)) + self.l_bound
            self.parameter_opts['wcm'] = wcm
            for individual in population:
                individual.genotype = copy(wcm)

            parameters = Parameters(**self.parameter_opts)
            functions['mutateParameters'] = parameters.adaptCovarianceMatrix

            _, results = baseAlgorithm(population, fitnessFunction, self.budget, functions, parameters)

        return results

def customizedES(n, fitnessFunction, budget, mu=None, lambda_=None, opts=None, values=None):
    """
        This function accepts a dictionary of options 'opts' which selects from a large range of different
        functions and combinations of those. Instrumental in Evolving Evolution Strategies

        :param n:               Dimensionality of the problem to be solved
        :param fitnessFunction: Function to determine the fitness of an individual
        :param budget:          Number of function evaluations allowed for this algorithm
        :param mu:              Number of individuals that form the parents of each generation
        :param lambda_:         Number of individuals in the offspring of each generation
        :param opts:            Dictionary containing the options (elitist, active, threshold, etc) to be used
        :param values:          Dictionary containing initial values for initializing (some of) the parameters
        :returns:               The statistics generated by running the algorithm
    """

    l_bound = ones((n, 1)) * -5
    u_bound = ones((n, 1)) * 5

    if lambda_ is None:
        lambda_ = int(4 + floor(3 * log(n)))
    eff_lambda = lambda_
    if mu is None:
        mu = 0.5

    # Boolean defaults, if not given
    bool_default_opts = ['active', 'elitist', 'mirrored', 'orthogonal', 'sequential', 'threshold', 'tpa']
    for op in bool_default_opts:
        if op not in opts:
            opts[op] = False

    # String defaults, if not given
    string_default_opts = ['base-sampler', 'ipop', 'selection', 'weights_option']
    for op in string_default_opts:
        if op not in opts:
            opts[op] = None

    if opts['tpa']:
        if lambda_ <= 4:
            lambda_ = 4
            eff_lambda = 2
        else:
            eff_lambda = lambda_ - 2

    if opts['selection'] == 'pairwise':
        selector = Sel.pairwise
        # Explicitly force lambda_ to be even
        if lambda_ % 2 == 1:
            lambda_ -= 1
            if lambda_ == 0:  # If lambda_ is too low, make it be at least one pair
                lambda_ += 2

        if opts['tpa']:
            if lambda_ == 2:
                lambda_ += 2
            eff_lambda = lambda_ - 2
        else:
            eff_lambda = lambda_

        if mu >= 0.5:  # We cannot select more than half of the population when only half is actually available
            mu /= 2
    else:
        selector = Sel.best

    def select(pop, new_pop, _, param):
        return selector(pop, new_pop, param)

    # Pick the lowest-level sampler
    if opts['base-sampler'] == 'quasi-sobol':
        sampler = Sam.QuasiGaussianSobolSampling(n)
    elif opts['base-sampler'] == 'quasi-halton' and Sam.halton_available:
        sampler = Sam.QuasiGaussianHaltonSampling(n)
    else:
        sampler = Sam.GaussianSampling(n)

    # Create an orthogonal sampler using the determined base_sampler
    if opts['orthogonal']:
        orth_lambda = eff_lambda
        if opts['mirrored']:
            orth_lambda = max(orth_lambda//2, 1)
        sampler = Sam.OrthogonalSampling(n, lambda_=orth_lambda, base_sampler=sampler)

    # Create a mirrored sampler using the sampler (structure) chosen so far
    if opts['mirrored']:
        sampler = Sam.MirroredSampling(n, base_sampler=sampler)

    parameter_opts = {'n': n, 'budget': budget, 'mu': mu, 'lambda_': lambda_, 'u_bound': u_bound, 'l_bound': l_bound,
                      'weights_option': opts['weights_option'], 'active': opts['active'], 'elitist': opts['elitist'],
                      'sequential': opts['sequential'], 'tpa': opts['tpa'], 'local_restart': opts['ipop'],
                      'values': values,
                      }

    # In case of pairwise selection, sequential evaluation may only stop after 2mu instead of mu individuals
    mu_int = int(1 + floor(mu*(eff_lambda-1)))
    if opts['sequential'] and opts['selection'] == 'pairwise':
        parameter_opts['seq_cutoff'] = 2
    population = [FloatIndividual(n) for _ in range(mu_int)]

    # Init all individuals of the first population at the same random point in the search space
    wcm = (np.random.randn(n,1) * (u_bound-l_bound)) + l_bound
    parameter_opts['wcm'] = wcm
    for individual in population:
        individual.genotype = copy(wcm)

    # We use functions/partials here to 'hide' the additional passing of parameters that are algorithm specific
    recombine = Rec.weighted
    mutate = partial(Mut.CMAMutation, sampler=sampler, threshold_convergence=opts['threshold'])

    functions = {
        'recombine': recombine,
        'mutate': mutate,
        'select': select,
    }

    if opts['ipop']:
        results = localRestartAlgorithm(fitnessFunction, budget, functions, parameter_opts)
    else:
        parameters = Parameters(**parameter_opts)
        functions['mutateParameters'] = parameters.adaptCovarianceMatrix

        _, results = baseAlgorithm(population, fitnessFunction, budget, functions, parameters)

    return results


def GA(n, fitnessFunction, budget, mu, lambda_, population, parameters=None):
    """
        Defines a Genetic Algorithm (GA) that evolves an Evolution Strategy (ES) for a given fitness function

        :param n:               Dimensionality of the search-space for the GA
        :param fitnessFunction: Fitness function the GA should use to evaluate candidate solutions
        :param budget:          The budget for the GA
        :param mu:              Population size of the GA
        :param lambda_:         Offpsring size of the GA
        :param population:      Initial population of candidates to be used by the MIES
        :param parameters:      Parameters object to be used by the GA
        :returns:               A tuple containing a bunch of optimization results
    """

    if parameters is None:
        parameters = Parameters(n=n, budget=budget, mu=mu, lambda_=lambda_)

    # We use functions here to 'hide' the additional passing of parameters that are algorithm specific
    recombine = Rec.random
    mutate = partial(Mut.mutateMixedInteger, options=options, num_options_per_module=num_options_per_module)
    best = Sel.bestGA
    def select(pop, new_pop, _, params):
        return best(pop, new_pop, params)
    def mutateParameters(_):
        pass  # The only actual parameter mutation is the self-adaptive step-size of each individual

    functions = {
        'recombine': recombine,
        'mutate': mutate,
        'select': select,
        'mutateParameters': mutateParameters,
    }

    _, results = baseAlgorithm(population, fitnessFunction, budget, functions, parameters,
                               parallel=Config.GA_evaluate_parallel)
    return results


def MIES(n, fitnessFunction, budget, mu, lambda_, population, parameters=None):
    """
        Defines a Mixed-Integer Evolution Strategy (MIES) that evolves an Evolution Strategy (ES) for a given fitness function

        :param n:               Dimensionality of the search-space for the MIES
        :param fitnessFunction: Fitness function the MIES should use to evaluate candidate solutions
        :param budget:          The budget for the MIES
        :param mu:              Population size of the MIES
        :param lambda_:         Offpsring size of the MIES
        :param population:      Initial population of candidates to be used by the MIES
        :param parameters:      Parameters object to be used by the MIES
        :returns:               A tuple containing a bunch of optimization results
    """

    if parameters is None:
        parameters = Parameters(n=n, budget=budget, mu=mu, lambda_=lambda_)

    # We use functions here to 'hide' the additional passing of parameters that are algorithm specific
    recombine = Rec.MIES_recombine
    mutate = partial(Mut.MIES_Mutate, options=options, num_options=num_options_per_module)
    best = Sel.bestGA

    def select(pop, new_pop, _, params):
        return best(pop, new_pop, params)

    def mutateParameters(_):
        pass  # The only actual parameter mutation is the self-adaptive step-size of each individual

    functions = {
        'recombine': recombine,
        'mutate': mutate,
        'select': select,
        'mutateParameters': mutateParameters,
    }

    _, results = baseAlgorithm(population, fitnessFunction, budget, functions, parameters,
                               parallel=Config.GA_evaluate_parallel)
    return results


def localRestartAlgorithm(fitnessFunction, budget, functions, parameter_opts, parallel=False):
    """
        Run the baseAlgorithm with the given specifications using a local-restart strategy.

        :param fitnessFunction: Function to determine the fitness of an individual
        :param budget:          Number of function evaluations allowed for this algorithm
        :param functions:       Dict with (lambda) functions 'recombine', 'mutate', 'select' and 'mutateParameters'
        :param parameter_opts:  Dictionary containing the all keyword options that will be used to initialize the
                                :class:`~code.Parameters.Parameters` object
        :param parallel:        Can be set to True to enable parallel evaluation. This disables sequential evaluation
        :return:                The statistics generated by running the algorithm
    """

    local_budget = budget
    best_fitness = float('inf')
    total_results = []

    if parameter_opts['lambda_']:
        lambda_init = parameter_opts['lambda_']
    elif parameter_opts['local_restart'] == 'IPOP' or parameter_opts['local_restart'] == 'BIPOP':
        lambda_init = int(4 + floor(3 * log(parameter_opts['n'])))
    else:
        lambda_init = None
    parameter_opts['lambda_'] = lambda_init

    # BIPOP Specific parameters
    lambda_large = lambda_init
    small_budget = None
    large_budget = None

    while local_budget > 0:

        # Every local restart needs its own parameters, so parameter update/mutation must also be linked every time
        parameters = Parameters(**parameter_opts)
        functions['mutateParameters'] = parameters.adaptCovarianceMatrix

        population = [FloatIndividual(parameters.n) for _ in range(parameters.mu_int)]

        # Init all individuals of the first population at the same random point in the search space
        wcm = (np.random.randn(parameters.n, 1) * (parameters.u_bound - parameters.l_bound)) + parameters.l_bound
        parameter_opts['wcm'] = wcm
        for individual in population:
            individual.genotype = copy(wcm)

        # Run the actual algorithm
        used_budget, local_results = baseAlgorithm(population, fitnessFunction, local_budget, functions, parameters,
                                                   parallel=parallel)
        local_budget -= used_budget

        # Extend all arrays returned
        if len(total_results) == 0:
            for result in local_results:  # generation_size, sigma_over_time, best_fitness_over_time, best_individual
                total_results.append(result)
            best_fitness = min(total_results[2])
        else:
            total_results[0].extend(local_results[0])
            total_results[1].extend(local_results[1])
            total_results[2].extend(local_results[2])
            if min(local_results[2]) < best_fitness:
                best_fitness = min(local_results[2])
                total_results[3] = local_results[3]


        # Increasing Population Strategies TODO: move these 'over-arching' parameters to a higher level object (???)
        if parameter_opts['local_restart'] == 'IPOP':
            parameter_opts['lambda_'] *= 2

        elif parameter_opts['local_restart'] == 'BIPOP':

            if small_budget is None:
                small_budget = local_budget // 2
                large_budget = local_budget - small_budget
                regime = 'large'
            else:
                if small_budget > large_budget > 0:
                    regime = 'small'
                else:
                    regime = 'large'

            if regime == 'large':
                large_budget -= used_budget
                lambda_large *= 2
                parameter_opts['lambda_'] = lambda_large
                parameter_opts['sigma'] = 2

            elif regime == 'small':
                small_budget -= used_budget
                rand_val = np.random.random() ** 2
                lambda_small = int(floor(lambda_init * (.5 * lambda_large/lambda_init)**rand_val))
                parameter_opts['lambda_'] = lambda_small
                parameter_opts['sigma'] = 2e-2*np.random.random()

    return tuple(total_results)


# Helper function
def _mutateAndEvaluate(ind, mutate, fitFunc):
    """
        Simple helper function for use by parallel running: mutate and evaluate an individual, since there is no
        use in waiting until all mutations are finished before we evaluate.

        :param ind:     The individual to mutate and evaluate
        :param mutate:  The mutation function to apply
        :param fitFunc: The fitness function to evaluate the individual with
        :return:        The original individual, with its genotype mutated and fitness stored inline
    """
    mutate(ind)
    ind.fitness = fitFunc(ind.genotype)[0]
    return ind


class _BaseAlgorithm(object):
    """
        Skeleton function for all ES algorithms
        Requires a population, fitness function handle, evaluation budget and the algorithm-specific functions

        The algorithm-specific functions should (roughly) behave as follows:
* ``recombine`` The current population (mu individuals) is passed to this function, and should return a new population (lambda individuals), generated by some form of recombination

* ``mutate`` An individual is passed to this function and should be mutated 'in-line', no return is expected

* ``select`` The original parents, new offspring and used budget are passed to this function, and should return a new population (mu individuals) after (mu+lambda) or (mu,lambda) selection

* ``mutateParameters`` Mutates and/or updates all parameters where required

:param population:      Initial set of individuals that form the starting population of the algorithm
:param fitnessFunction: Function to determine the fitness of an individual
:param budget:          Number of function evaluations allowed for this algorithm
:param functions:       Dict with (lambda) functions 'recombine', 'mutate', 'select' and 'mutateParameters'
:param parameters:      Parameters object for storing relevant settings
:param parallel:        Can be set to True to enable parallel evaluation. This disables sequential evaluation
:returns:               The statistics generated by running the algorithm
    """

    def __init__(self):
        pass

    def initialize(self, population, functions, parameters, parallel=False):
        # Parameter tracking
        self.sigma_over_time = []
        self.fitness_over_time = []
        self.generation_size = []
        self.best_individual = population[0]

        # Initialization
        self.seq_cutoff = parameters.mu_int * parameters.seq_cutoff
        self.used_budget = 0
        self.recombine = functions['recombine']
        self.mutate = functions['mutate']
        self.select = functions['select']
        self.mutateParameters = functions['mutateParameters']
        self.parallel = parallel

        # Single recombination outside the eval loop to create the new population
        self.new_population = self.recombine(population, parameters)


    def __call__(self, population, fitnessFunction, budget, functions, parameters, parallel=False):

        self.initialize(population, functions, parameters, parallel)

        # The main evaluation loop
        while self.used_budget < budget:

            if parameters.tpa:
                self.new_population = self.new_population[:-2]

            if self.parallel:

                for ind in self.new_population:
                    self.mutate(ind, parameters)
                fitnesses = fitnessFunction(
                    [ind.genotype for ind in self.new_population])  # Assumption: fitnessFunction is parallelized
                for j, ind in enumerate(self.new_population):
                    ind.fitness = fitnesses[j]

                self.used_budget += parameters.lambda_
                i = parameters.lambda_

            else:  # Sequential
                improvement_found = False
                for i, individual in enumerate(self.new_population):
                    self.mutate(individual, parameters)  # Mutation
                    # Evaluation
                    individual.fitness = fitnessFunction(individual.genotype)[
                        0]  # fitnessFunction returns a list, to allow
                    self.used_budget += 1  # evaluation of >1 individuals in 1 call

                    # Sequential Evaluation
                    if parameters.sequential:  # Sequential evaluation: we interrupt once a better individual has been found
                        if individual.fitness < self.best_individual.fitness:
                            improvement_found = True
                        if i >= self.seq_cutoff and improvement_found:
                            break
                        if self.used_budget == budget:
                            break

            self.new_population = self.new_population[:i + 1]  # Any un-used individuals in the new population are discarded
            fitnesses = sorted([individual.fitness for individual in self.new_population])
            population = self.select(population, self.new_population, self.used_budget, parameters)  # Selection

            # Track parameters
            gen_size = self.used_budget - len(self.fitness_over_time)
            self.generation_size.append(gen_size)
            self.sigma_over_time.extend([parameters.sigma_mean] * gen_size)
            self.fitness_over_time.extend([population[0].fitness] * gen_size)
            if population[0].fitness < self.best_individual.fitness:
                self.best_individual = copy(population[0])

            # We can stop here if we know we reached our budget
            if self.used_budget >= budget:
                break

            if len(population) == parameters.mu_int:
                self.new_population = self.recombine(population, parameters)  # Recombination
            else:
                print('Error encountered in baseAlgorithm():\n'
                      'Bad population size! Size: {} instead of {} at used budget {}'.format(len(population),
                                                                                             parameters.mu_int,
                                                                                             self.used_budget))

            # Two-Point step-size Adaptation
            # TODO: Move the following code to >= 1 separate function(s)
            if parameters.tpa:
                wcm = parameters.wcm
                tpa_vector = (wcm - parameters.wcm_old) * parameters.tpa_factor

                tpa_fitness_plus = fitnessFunction(wcm + tpa_vector)[0]
                tpa_fitness_min = fitnessFunction(wcm - tpa_vector)[0]

                self.used_budget += 2
                if self.used_budget > budget and parameters.sequential:
                    self.used_budget = budget

                # Is the ideal step size larger (True) or smaller (False)? None if TPA is not used
                if tpa_fitness_plus < tpa_fitness_min:
                    parameters.tpa_result = 1
                else:
                    parameters.tpa_result = -1

            self.mutateParameters(self.used_budget)  # Parameter mutation

            # Local restart
            if parameters.localRestart(self.used_budget, fitnesses):
                break

        return self.used_budget, (self.generation_size, self.sigma_over_time, self.fitness_over_time, self.best_individual)


def baseAlgorithm(population, fitnessFunction, budget, functions, parameters, parallel=False):
    """
        Skeleton function for all ES algorithms
        Requires a population, fitness function handle, evaluation budget and the algorithm-specific functions

        The algorithm-specific functions should (roughly) behave as follows:
* ``recombine`` The current population (mu individuals) is passed to this function, and should return a new population (lambda individuals), generated by some form of recombination

* ``mutate`` An individual is passed to this function and should be mutated 'in-line', no return is expected

* ``select`` The original parents, new offspring and used budget are passed to this function, and should return a new population (mu individuals) after (mu+lambda) or (mu,lambda) selection

* ``mutateParameters`` Mutates and/or updates all parameters where required

:param population:      Initial set of individuals that form the starting population of the algorithm
:param fitnessFunction: Function to determine the fitness of an individual
:param budget:          Number of function evaluations allowed for this algorithm
:param functions:       Dict with (lambda) functions 'recombine', 'mutate', 'select' and 'mutateParameters'
:param parameters:      Parameters object for storing relevant settings
:param parallel:        Can be set to True to enable parallel evaluation. This disables sequential evaluation
:returns:               The statistics generated by running the algorithm
    """

    baseAlg = _BaseAlgorithm()
    return baseAlg(population, fitnessFunction, budget, functions, parameters, parallel)
