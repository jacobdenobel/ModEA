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
    population = [FloatIndividual(n) for _ in range(parameters.mu_int)]

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

    if opts is None:
        opts = dict()

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


class EvolutionaryOptimizer(object):
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
:param functions:       Dictionary with functions 'recombine', 'mutate', 'select' and 'mutateParameters'
:param parameters:      Parameters object for storing relevant settings
:param parallel:        Set to True to enable parallel evaluation. Note: this disables sequential evaluation
:returns:               The statistics generated by running the algorithm
    """

    def __init__(self, population, fitnessFunction, budget, functions, parameters, parallel=False):
        # Initialization
        self.seq_cutoff = parameters.mu_int * parameters.seq_cutoff
        self.used_budget = 0
        self.recombine = functions['recombine']
        self.mutate = functions['mutate']
        self.select = functions['select']
        self.mutateParameters = functions['mutateParameters']
        if population:
            self.population = population
        else:
            self.initializePopulation()
        self.parameters = self.instantiateParameters(parameters)
        self.new_population = self.recombine(self.population, self.parameters)
        self.fitnessFunction = fitnessFunction
        self.budget = budget
        self.parallel = parallel

        # Parameter tracking
        self.sigma_over_time = []
        self.fitness_over_time = []
        self.generation_size = []
        self.best_individual = population[0]


    def instantiateParameters(self, params):
        if isinstance(params, Parameters):
            return params
        elif isinstance(params, dict):
            return Parameters(**params)


    def evalPopulation(self):
        for ind in self.new_population:
            self.mutate(ind, self.parameters)
        fitnesses = self.fitnessFunction([ind.genotype for ind in self.new_population])
        for ind, fit in zip(self.new_population, fitnesses):
            ind.fitness = fit

        self.used_budget += self.parameters.lambda_


    def evalPopulationSequentially(self):
        improvement_found = False
        for i, individual in enumerate(self.new_population):
            self.mutate(individual, self.parameters)  # Mutation
            # Evaluation
            individual.fitness = self.fitnessFunction(individual.genotype)[0]
            self.used_budget += 1  # evaluation of >1 individuals in 1 call

            # Sequential Evaluation
            if self.parameters.sequential:  # We interrupt once a better individual has been found
                if individual.fitness < self.best_individual.fitness:
                    improvement_found = True
                if i >= self.seq_cutoff and improvement_found:
                    break
                if self.used_budget == self.budget:
                    break
        self.new_population = self.new_population[:i+1]  # Discard unused individuals


    def tpaUpdate(self):
        wcm = self.parameters.wcm
        tpa_vector = (wcm - self.parameters.wcm_old) * self.parameters.tpa_factor

        tpa_fitness_plus = self.fitnessFunction(wcm + tpa_vector)[0]
        tpa_fitness_min = self.fitnessFunction(wcm - tpa_vector)[0]

        self.used_budget += 2
        if self.used_budget > self.budget and self.parameters.sequential:
            self.used_budget = self.budget

        # Is the ideal step size larger (True) or smaller (False)? None if TPA is not used
        if tpa_fitness_plus < tpa_fitness_min:
            self.parameters.tpa_result = 1
        else:
            self.parameters.tpa_result = -1


    def recordStatistics(self):
        gen_size = self.used_budget - len(self.fitness_over_time)
        self.generation_size.append(gen_size)
        self.sigma_over_time.extend([self.parameters.sigma_mean] * gen_size)
        self.fitness_over_time.extend([self.population[0].fitness] * gen_size)
        if self.population[0].fitness < self.best_individual.fitness:
            self.best_individual = copy(self.population[0])


    def runOneGeneration(self):
        if self.parameters.tpa:
            self.new_population = self.new_population[:-2]

        if self.parallel:
            self.evalPopulation()
        else:  # Sequential
            self.evalPopulationSequentially()

        self.parameters.recordRecentFitnessValues(self.used_budget, [ind.fitness for ind in self.new_population])

        self.population = self.select(self.population, self.new_population, self.used_budget, self.parameters)
        self.new_population = self.recombine(self.population, self.parameters)

        if self.parameters.tpa:  # Two-Point step-size Adaptation
            self.tpaUpdate()

        self.mutateParameters(self.used_budget)


    def runOptimizer(self):
        # The main evaluation loop
        while self.used_budget < self.budget and not self.parameters.checkLocalRestartConditions(self.used_budget):
            self.runOneGeneration()
            self.recordStatistics()


    def initializePopulation(self):
        self.population = [FloatIndividual(self.parameters.n) for _ in range(self.parameters.mu_int)]
        # Init all individuals of the first population at the same random point in the search space
        wcm = (np.random.randn(self.parameters.n, 1) * (self.parameters.u_bound - self.parameters.l_bound)) + self.parameters.l_bound
        for individual in self.population:
            individual.genotype = copy(wcm)


    def runLocalRestartOptimizer(self):
        """
            Run the baseAlgorithm with the given specifications using a local-restart strategy.
        """

        parameter_opts = self.parameters.getParameterOpts()

        local_budget = self.budget

        if parameter_opts['lambda_']:
            lambda_init = parameter_opts['lambda_']
        elif parameter_opts['local_restart'] in ['IPOP', 'BIPOP']:
            lambda_init = int(4 + floor(3 * log(parameter_opts['n'])))
        else:
            lambda_init = None
        parameter_opts['lambda_'] = lambda_init

        # BIPOP Specific parameters
        self.lambda_ = {'small': None, 'large': lambda_init}
        self.budgets = {'small': None, 'large': None}
        self.regime = 'first'  # Later alternates between 'large' and 'small'

        while local_budget > 0:

            # Every local restart needs its own parameters, so parameter update/mutation must also be linked every time
            self.parameters = Parameters(**parameter_opts)
            self.mutateParameters = self.parameters.adaptCovarianceMatrix

            self.initializePopulation()
            parameter_opts['wcm'] = self.population[0].genotype

            # Run the actual algorithm
            prev_budget = self.used_budget
            self.runOptimizer()
            used_budget = self.used_budget - prev_budget
            local_budget -= used_budget

            # Increasing Population Strategies
            if parameter_opts['local_restart'] == 'IPOP':
                parameter_opts['lambda_'] *= 2

            elif parameter_opts['local_restart'] == 'BIPOP':

                try:
                    self.budgets[self.regime] -= used_budget
                    self.regime = 'small' if self.budgets['small'] > self.budgets['large'] > 0 else 'large'
                except KeyError:  # Setup of the two regimes after running regularily for the first time
                    self.budgets['small'] = local_budget // 2
                    self.budgets['large'] = local_budget - self.budgets['small']
                    self.regime = 'large'

                if self.regime == 'large':
                    self.lambda_['large'] *= 2
                    parameter_opts['sigma'] = 2
                elif self.regime == 'small':
                    rand_val = np.random.random() ** 2
                    self.lambda_['small'] = int(floor(lambda_init * (.5 * self.lambda_['large'] / lambda_init) ** rand_val))
                    parameter_opts['sigma'] = 2e-2 * np.random.random()

                parameter_opts['lambda'] = self.lambda_[self.regime]


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

    baseAlg = EvolutionaryOptimizer(population, fitnessFunction, budget, functions, parameters, parallel)
    baseAlg.runOptimizer()
    return baseAlg.used_budget, (baseAlg.generation_size, baseAlg.sigma_over_time,
                                 baseAlg.fitness_over_time, baseAlg.best_individual)


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
    functions['mutateParameters'] = None  # None to prevent KeyError, will be set later
    baseAlg = EvolutionaryOptimizer(None, fitnessFunction, budget, functions, parameter_opts, parallel)
    baseAlg.runOptimizer()
    return baseAlg.generation_size, baseAlg.sigma_over_time, baseAlg.fitness_over_time, baseAlg.best_individual
