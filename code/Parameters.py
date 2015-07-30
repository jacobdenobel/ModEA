__author__ = 'Sander van Rijn <svr003@gmail.com>'

import numpy as np


class Parameters(object):
    """
        Data holder class that initializes *all* possible parameters, regardless of what functions/algorithm are used
        If multiple functions/algorithms use the same parameter name, but differently, these will be split into
        separate parameters.
    """


    def __init__(self, n, mu, lambda_, plus_selection=False):
        """
            Setup the set of parameters
        """
        ### Basic parameters ###
        self.n = n
        self.mu = mu
        self.lambda_ = lambda_
        self.sigma = 1
        self.plus_selection = plus_selection

        ### Meta-parameters ###
        self.N = 10 * self.n

        ### (1+1)-ES ###
        self.success_history = np.zeros((self.N, ), dtype=np.int)
        self.c = 0.817  # Sigma adaptation factor

        ### CMA-ES ###
        self.C = np.eye(n)  # Covariance matrix
        self.B = np.eye(n)  # Eigenvectors of C
        self.D = np.eye(n)  # Diagonal eigenvalues of C
        self.s_mean = None

        ### CMSA-ES ###
        self.tau = 1 / np.sqrt(2*n)
        self.tau_c = 1 + ((n**2 + n) / (2*mu))
        self.sigma_mean = self.sigma

        ### (1+1)-Cholesky ES ###
        self.A = np.eye(n)
        self.d = 1 + n/2
        self.p_target = 2/11
        self.p_success = self.p_target
        self.p_c = np.zeros((1,n))
        self.c_p = 1/12
        self.c_cov = 2 / (n**2 + 6)
        self.p_thresh = 0.44
        self.c_a = np.sqrt(1 - self.c_cov)
        self.lambda_success = False
        self.last_z = np.zeros((1,n))  # To be recorded by the mutation

        ### Active (1+1)CMA-ES ###
        self.A_inv = np.eye(n)
        self.s = np.zeros((1,n))
        self.fitness_history = []  # 'Filler' data
        self.best_fitness = np.inf
        self.c_act = 2/(n+2)
        self.c_cov_pos = 2/(n**2 + 6)
        self.c_cov_neg = 0.4/(n**1.6 + 1)


    def oneFifthRule(self, t):
        """
            Adapts sigma based on the 1/5-th success rule
        """

        # Only adapt every n evaluations
        if t % self.n != 0:
            return


        if t < self.N:
            success = np.mean(self.success_history[:t])
        else:
            success = np.mean(self.success_history)

        if success < 1/5:
            self.sigma *= self.c
        elif success > 1/5:
            self.sigma /= self.c

        self.sigma_mean = self.sigma


    def addToSuccessHistory(self, t, success):
        """
            Record the (boolean) 'success' value at time 't'
        """

        t %= self.N
        self.success_history[t] = 1 if success else 0
        self.lambda_success = success  # For 1+1 Cholesky CMA ES


    def addToFitnessHistory(self, t, fitness):
        """
            Record the latest fitness value (with a history of 5 generations)
        """

        self.fitness_history.append(fitness)
        if len(self.fitness_history) > 5:
            self.fitness_history = self.fitness_history[1:]


    def adaptCovarianceMatrix(self):
        """
            Adapt the covariance matrix according to the CMSA-ES
        """

        tau_c_inv = 1/self.tau_c

        self.C *= (1 - tau_c_inv)
        self.C += tau_c_inv * (self.s_mean.T * self.s_mean)

        self.checkDegenerated()


    def adaptCholeskyCovarianceMatrix(self):
        """
            Adapt the covariance matrix according to the Cholesky CMA-ES
        """

        self.p_success = (1 - self.c_p)*self.p_success + self.c_p*int(self.lambda_success)
        self.sigma *= np.exp((self.p_success - (self.p_target/(1-self.p_target))*(1-self.p_success))/self.d)
        self.sigma_mean = self.sigma

        if self.lambda_success and self.p_success < self.p_thresh:
            # Helper variables
            z_squared = np.linalg.norm(self.last_z) ** 2
            c_a_squared = self.c_a ** 2

            part_1 = self.c_a / z_squared
            part_2 = np.sqrt(1 + (((1 - c_a_squared)*z_squared) / c_a_squared)) - 1
            part_3 = np.dot(np.dot(self.A, self.last_z.T), self.last_z)

            # Actual matrix update
            self.A = self.c_a*self.A + part_1*part_2*part_3

        self.checkCholeskyDegenerated()


    def adaptActiveCovarianceMatrix(self):
        """
            Adapt the covariance matrix according to the Cholesky CMA-ES
        """

        # Positive Cholesky update
        if self.lambda_success:
            self.p_success = (1 - self.c_p)*self.p_success + self.c_p
            self.s = (1-self.c)*self.s + np.sqrt(self.c * (2-self.c)) * np.dot(self.A, self.last_z.T)

            w = np.dot(self.A_inv, self.s.T)
            w_norm_squared = np.linalg.norm(w)**2
            a = np.sqrt(1 - self.c_cov_pos)
            b = (a/w_norm_squared) * (np.sqrt(1+w_norm_squared * self.c_cov_pos / (1-self.c_cov_pos)) - 1)  #TODO check division/multiplication order

            self.A = a*self.A + b*np.dot(np.dot(self.A, w), w.T)
            self.A_inv = (1/a)*self.A_inv - b/(a**2 + a*b*w_norm_squared) * np.dot(w, np.dot(w.T, self.A_inv))

        else:
            self.p_success *= (1-self.c_p)

        self.sigma *= np.exp((1/self.d) * ((self.p_success-self.p_target) / (1-self.p_target)))
        self.sigma_mean = self.sigma

        # Negative Cholesky update
        if len(self.fitness_history) > 4 and self.fitness_history[-1] < self.best_fitness:
            # Helper variables
            z_squared = np.linalg.norm(self.last_z) ** 2

            if self.c_cov_neg*(2*z_squared -1) > 1:
                self.c_cov_neg = 1/(2*z_squared - 1)
            else:
                self.c_cov_neg = 0.4/(self.n**1.6 + 1)  # TODO: currently hardcoded copy of default value

            c_cov_neg = self.c_cov_neg
            w = np.dot(self.A_inv, self.s.T)
            a = np.sqrt(1+self.c_cov_neg)
            b = (a/z_squared) * (np.sqrt(1 + (c_cov_neg*z_squared) / (1+c_cov_neg)) - 1)
            self.A = a*self.A + b*np.dot(np.dot(self.A, w), w.T)
            self.A_inv = (1/a)*self.A_inv - b/(a**2 + a*b*(np.linalg.norm(w)**2) * np.dot(w, np.dot(w.T, self.A_inv)))

        self.checkCholeskyDegenerated()


    def checkDegenerated(self):
        """
            Check if the parameters (C, s_mean, etc) have degenerated and need to be reset
        """

        degenerated = False

        if np.min(np.isfinite(self.C)) == 0:
            degenerated = True

        elif not ((10**(-16)) < self.sigma_mean < (10**16)):
            degenerated = True

        else:
            self.D, self.B = np.linalg.eig(self.C)
            self.D = np.sqrt(self.D)
            if not np.isreal(self.D).all():
                degenerated = True


        if degenerated:
            n = self.n

            self.C = np.eye(n)
            self.B = np.eye(n)
            self.D = np.eye(n)
            self.sigma_mean = 1          # TODO: make this depend on any input default sigma value

            # TODO: add feedback of resetting sigma to the sigma per individual


    def checkCholeskyDegenerated(self):
        """
            Check if the parameters (C, s_mean, etc) have degenerated and need to be reset
        """

        degenerated = False

        if np.min(np.isfinite(self.A)) == 0:
            degenerated = True

        elif not ((10 ** (-16)) < np.linalg.cond(self.A) < (10 ** 16)):
            degenerated = True

        elif not ((10 ** (-16)) < self.sigma_mean < (10 ** 16)):
            degenerated = True


        if degenerated:
            n = self.n

            self.sigma_mean = 1  # TODO: make this depend on any input default sigma value

            self.p_success = self.p_target
            self.A = np.eye(n)
            self.p_c = np.zeros((1, n))


    def checkActiveDegenerated(self):
        """
            Check if the parameters (C, s_mean, etc) have degenerated and need to be reset
        """

        degenerated = False

        if np.linalg.cond(np.dot(self.A, self.A.T)) > (10 ** 14):
            degenerated = True

        elif not ((10 ** (-16)) < self.sigma_mean < (10 ** 16)):
            degenerated = True

        if degenerated:
            n = self.n

            self.A = np.eye(n)
            self.A_inv = np.eye(n)
            self.sigma_mean = 1
            self.p_success = 0
            self.s = np.zeros((1,n))

            self.fitness_history = self.best_fitness * np.ones((5,1))
