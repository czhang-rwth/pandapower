# -*- coding: utf-8 -*-

# Copyright (c) 2016-2019 by University of Kassel and Fraunhofer Institute for Energy Economics
# and Energy System Technology (IEE), Kassel. All rights reserved.

import numpy as np
try:
    from numba import jit
except ImportError:
    from pandapower.pf.no_numba import jit

from scipy.stats import chi2

from pandapower.estimation.algorithm.matrix_base import BaseAlgebra
from pandapower.estimation.ppc_conversion import ExtendedPPCI


def get_estimator(base_class, estimator_name):
    assert base_class in (BaseEstimatorIRWLS, BaseEstimatorOpt)

    available_estimators = {estm_cls.__name__.split("Estimator")[0].lower(): estm_cls
                            for estm_cls in base_class.__subclasses__()}
    if estimator_name.lower() not in available_estimators:
        raise Exception("Estimator not available! Try another one!")
    else:
        return available_estimators[estimator_name.lower()]


class BaseEstimatorIRWLS(BaseAlgebra):
    def __init__(self, eppci: ExtendedPPCI, **hyperparameters):
        # Initilize BaseAlgebra object for calculation of relevant matrix
        super(BaseEstimatorIRWLS, self).__init__(eppci)

    def create_phi(self, E):
        # Must be implemented!
        pass


class BaseEstimatorOpt(BaseAlgebra):
    def __init__(self, eppci, **hyperparameters):
        super(BaseEstimatorOpt, self).__init__(eppci)
        # Hyperparameters for estimator should be added here

    def cost_function(self, E):
        # Minimize sum(cost(r))
        # r = cost(z - h(x))
        # Must be implemented according to the estimator for the optimization
        pass

    def create_rx_jacobian(self, E):
        pass


class WLSEstimator(BaseEstimatorOpt, BaseEstimatorIRWLS):
    def __init__(self, eppci, **hyperparameters):
        super(WLSEstimator, self).__init__(eppci, **hyperparameters)

    def cost_function(self, E):
        rx = self.create_rx(E)
        cost = np.sum((1/self.sigma**2) * (rx**2))
        return cost

    def create_rx_jacobian(self, E):
        # dr/dE = drho / dr * d(z-hx) / dE
        # dr/dE = (drho/dr) * - (d(hx)/dE)
        # 2 * rx * -(dhx/dE)
        rx = self.create_rx(E)
        hx_jac = self.create_hx_jacobian(E)
        jac = - np.sum(2 * rx.reshape((-1, 1)) * hx_jac, axis=0)
        return jac

    def create_phi(self, E):
        # Standard WLS does not update this matrix
        return np.diagflat(1/self.sigma**2)


class SHGMEstimatorIRWLS(BaseEstimatorIRWLS):
    # Still need test!
    def __init__(self, eppci: ExtendedPPCI, **hyperparameters):
        super(SHGMEstimatorIRWLS, self).__init__(eppci, **hyperparameters)
        assert 'a' in hyperparameters
        self.a = hyperparameters.get('a')

    def create_phi(self, E):
        r = self.create_rx(E)
        chi2_res, w = self.weight(E)
        rsi = r / (w * self.sigma)
        phi = 1/(self.sigma**2)
        condition_mask = np.abs(rsi)>self.a
        phi[condition_mask] = (1/(self.sigma**2) * np.abs(self.a / rsi))[condition_mask] 
        return np.diagflat(phi)

    def weight(self, E):
        H = self.create_hx_jacobian(E)
        v = np.sum(H != 0, axis=1)
        chi2_res = chi2.ppf(0.975, v)
        ps = self._ps(H)
        return chi2_res, np.min(np.c_[(chi2_res/ps)**2, np.ones(ps.shape)], axis=1)

    def _ps(self, H):
        omega = np.dot(H, H.T)

        x = np.zeros(omega.shape[0]-1)
        y = np.zeros(omega.shape[0])
        sm = np.zeros(omega.shape[0])
        ps = np.zeros(omega.shape[0])

        @jit(nopython=True)
        def calc_sm(omega, x, y, sm):
            m = omega.shape[0]
            x_shape = x.shape[0]
            y_shape = y.shape[0]
            count0 = 0
            for k in range(m):
                for i in range(m):
                    count0 = 0
                    for j in range(m):
                        if j != i:
                            x_ix = j if j < i else j-1
                            x[x_ix] = np.abs(omega[i, k]+omega[j, k])
                            if not x[x_ix]:
                                count0 += 1
                    y[i] = np.sort(x)[count0 + (x_shape - count0 + 1)//2 - 1]
                sm[k] = np.sort(y)[(y_shape + 1)//2 - 1] * 1.1926
            return sm

        sm = calc_sm(omega, x, y, sm)
        for i in range(omega.shape[0]):
            ps[i] = np.max(np.abs(omega[i, :])/sm)
        return ps


class LAVEstimator(BaseEstimatorOpt):
    def cost_function(self, E):
        rx = self.create_rx(E)
        cost = np.sum(np.abs(rx))
        return cost

    def create_rx_jacobian(self, E):
        # dr/dE = drho / dr * d(z-hx) / dE
        # dr/dE = (drho/dr) * - (d(hx)/dE)
        # sign(rx) * -(dhx/dE)
        rx = self.create_rx(E)
        hx_jac = self.create_hx_jacobian(E)
        jac = - np.sum(np.sign(rx.reshape((-1, 1))) * hx_jac, axis=0)
        return jac


class QCEstimatorOpt(BaseEstimatorOpt):
    def __init__(self, eppci, **hyperparameters):
        super(QCEstimatorOpt, self).__init__(eppci, **hyperparameters)
        assert 'a' in hyperparameters
        self.a = hyperparameters['a']

    def cost_function(self, E):
        rx = self.create_rx(E)
        cost = (1/self.sigma**2) * (rx**2)
        if np.any(np.abs(rx/self.sigma) > self.a):
            cost[np.abs(rx/self.sigma) > self.a] = (self.a**2 / self.sigma**2)[np.abs(rx/self.sigma) > self.a]
#        print(np.sum(cost))
        return np.sum(cost)

    def create_rx_jacobian(self, E):
        # dr/dE = drho / dr * d(z-hx) / dE
        # dr/dE = (drho/dr) * - (d(hx)/dE)
        # 2 * rx * -(dhx/dE) if np.abs(rx/sigma) < a
        # 0 else
        rx = self.create_rx(E)
        hx_jac = self.create_hx_jacobian(E)
        drho = 2 * rx.reshape((-1, 1))
        if np.any(np.abs(rx/self.sigma) > self.a):
            drho[np.abs(rx/self.sigma) > self.a] = 0
        jac = - np.sum(drho * hx_jac, axis=0)
        return jac


class QLEstimatorOpt(BaseEstimatorOpt):
    def __init__(self, eppci, **hyperparameters):
        super(QLEstimatorOpt, self).__init__(eppci, **hyperparameters)     
        assert 'a' in hyperparameters
        self.a = hyperparameters['a']

    def cost_function(self, E):
        rx = self.create_rx(E)
        cost = (1/self.sigma**2) * (rx**2)
        if np.any(np.abs(rx/self.sigma) > self.a):
            cost[np.abs(rx/self.sigma) > self.a] = (2*self.a*self.sigma*np.abs(rx) -\
                self.a**2 * self.sigma**2)[np.abs(rx/self.sigma) > self.a]
#        print(np.sum(cost))
        return np.sum(cost)

    def create_rx_jacobian(self, E):
        # dr/dE = drho / dr * d(z-hx) / dE
        # dr/dE = (drho/dr) * - (d(hx)/dE)
        # 2 * rx * -(dhx/dE) if np.abs(rx/sigma) < a
        # 0 else
        rx = self.create_rx(E)
        hx_jac = self.create_hx_jacobian(E)
        drho = 2 * rx.reshape((-1, 1))
        if np.any(np.abs(rx/self.sigma) > self.a):
            drho[np.abs(rx/self.sigma) > self.a] =\
                - np.sum((2*self.a*self.sigma*np.sign(rx)).reshape((-1, 1)) * hx_jac, 
                         axis=0)[np.abs(rx/self.sigma) > self.a]
        jac = - np.sum(drho * hx_jac, axis=0)  
        return jac