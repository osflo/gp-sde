import torch
from torch import nn
from modules import KullbackLeibler
from settings import var_init, float_type
from utils import train_model
import pickle

import time
# ------- Outer SDE model class containing training, plotting, prediction attributes --------


class GPSDE(object):
    def __init__(self, model, inference):
        super(GPSDE, self).__init__()
        """
        class for learning and inference in a Stochastic Differential equation using a Gaussian Process approximation
        The generative model is of the form

        dx(t) = f(x) dt + dW(t) where W(t) is Wiener noise  (latent SDE with transfer function f(x))
        h(t)  = g(x)                                        (output mapping from low-d to high-d)
        Y ~ p(Y|h)                                          (observation model / likelihood)

        input:
        ------
        model         -- GPSDE model (pytorch)
        inference     -- inference algorithm of choice (needs to be able to deal with pytorch variables as input)
        """

        self.model = model
        self.inference = inference
        # initialise initial state in inference
        self.inference.get_initialState(self.model.initialMean, self.model.initialCov)
        # create empty lists for monitoring behavior of algorithm
        self.NegativeFreeEngery = []
        self.KLdiv = []
        self.expectedLogLik = []
        self.KLinit = []
        self.transfuncPrior = []
        self.outMapPrior = []

    def variationalEM(self, niter=100, eStepIter=10, mStepIter=10):
        time0=time.time()
        for i in range(niter):
            # run inference across all trials
            self.inference_update(eStepIter)
            time1=time.time()
           # print("inference time="+str(time1-time0))

            # run learning of transferfunction and other model parameters, hyperparameters
            final_ell, final_kld, final_prior_trans, _ = self.learning_update(mStepIter)
            time2=time.time()
            #print('learning time='+str(time2-time1))
            #torch.save(self.model,'model_for_error/model_1_iteration.pt')

            # update initial state of latent
            self.initialState_update()
            time3=time.time()
           # print("update time"+str(time3-time2))
            
            # print some output and store cost function values
            self.callback(i, final_ell, final_kld, final_prior_trans)
            
            #torch.save(self.model,'Model_by_iter/model_'+str(i)+'.pt')
            #with open('Model_by_iter/inference_'+str(i)+'.pt','wb') as file:
            #    pickle.dump(self.inference,file)

            #with open('Model_by_iter/all_'+str(i)+'.pt','wb') as file:
              #  pickle.dump(self,file)

        # final inference pass
        self.inference_update(eStepIter)

    def learning_update(self, niterM):
        # calls train_model() and optimizes leaning given current variational approx
        # get inference results
        inputs = self.model.collectInferenceResults(self.inference)

        # perforn closed form updates of transition function parmeters
        self.model.closedFormUpdates(*inputs)
        #torch.save(self.model,'model_for_error/model_after_closed_form_update.pt')
        #torch.save(self.model.transfunc,'model_for_error/transf_after_closed_form_update.pt')
        # train model parameters
        final_ell, final_kld, final_prior_trans, final_prior_map = train_model(self.model, inputs, niterM)
        # refresh any values that are stored throughout inference but updated during learning
        self.model.transfunc.refresh_stored_values()
        self.model.outputMapping.refresh_stored_values()

        # return values of each term in cost function for diagnostics
        return final_ell, final_kld, final_prior_trans, final_prior_map

    def inference_update(self, niterE):
        self.inference.run_inference(self.model, niterE)

    def initialState_update(self):
        for idx in range(self.model.like.nTrials):
            self.model.initialMean[idx] = self.inference.initialMean[idx][:]
            self.model.initialCov[idx] = self.inference.initialCov[idx][:]

    def callback(self, iter, final_ell, final_kld, final_prior_trans):
        # evaluate cost and print value
        self.NegativeFreeEngery.append(-final_ell + final_kld - final_prior_trans)
        self.KLdiv.append(final_kld)
        self.expectedLogLik.append(final_ell)
        self.transfuncPrior.append(final_prior_trans)
        if iter == 0:
            dash = '-' * 55
            print(dash)
            print('{:<4s}{:>12s}{:>12s}{:>12s}{:>12s}'.format('iter', 'objective', 'log-like', 'kl-div',
                                                              'f-prior'))
            print(dash)

        print('{:>4d}{:>12.3f}{:>12.3f}{:>12.3f}{:>12.3f}'.format(iter, self.NegativeFreeEngery[-1], self.expectedLogLik[-1], self.KLdiv[-1], self.transfuncPrior[-1]))

# ------- General SDE model class containing forward pass for learning --------


class GPSDEmodel(nn.Module):
    """
    General GPSDE class. Observations are sampled sparsely and potentially unevenly at known locations.
    """

    def __init__(self, nLatent, transfunc, outputMapping, like, nLeg=50):
        super(GPSDEmodel, self).__init__()

        self.outputMapping = outputMapping
        self.transfunc = transfunc
        self.like = like
        self.nLatent = nLatent
        self.KLdiv = KullbackLeibler(self.like.trLen, nLeg=nLeg)
        self.initialiseInitialState()
        torch.set_default_dtype(float_type)

    def initialiseInitialState(self):
        self.initialMean = [torch.zeros(1, self.nLatent).type(float_type) for _ in range(self.like.nTrials)]  # 1 x K
        self.initialCov = [var_init * torch.eye(self.nLatent).type(float_type) for _ in range(self.like.nTrials)]  # K x K

    def collectInferenceResults(self, inference):

        m_sp = [[] for _ in range(inference.nTrials)]
        S_sp = [[] for _ in range(inference.nTrials)]

        nquad = self.KLdiv.xxLeg.size()[1]  # number of quadrature nodes for time integrals

        # create empty tensors to store predicted means and variances across all trials
        m_qu = torch.zeros(inference.nTrials, nquad, 1, inference.nLatent)
        S_qu = torch.zeros(inference.nTrials, nquad, inference.nLatent, inference.nLatent)
        A_qu = torch.zeros(inference.nTrials, nquad, inference.nLatent, inference.nLatent)
        b_qu = torch.zeros(inference.nTrials, nquad, 1, inference.nLatent)

        for idx in range(inference.nTrials):
            sample_times = self.like.tObs[idx]
            quad_times = self.KLdiv.xxLeg[idx]
            # get marginals

            m_sp[idx], S_sp[idx] = inference.predict_marginals(idx, sample_times)
            m_qu[idx, :, :, :], S_qu[idx, :, :, :] = inference.predict_marginals(idx, quad_times)
            A_qu[idx, :, :, :], b_qu[idx, :, :, :] = inference.predict_conditionalParams(idx, quad_times)

        return (m_sp, S_sp, m_qu, S_qu, A_qu, b_qu)  # output tuple

    def closedFormUpdates(self, m_sp, S_sp, m_qu, S_qu, A_qu, b_qu):
        # update transition function parameters if possible
        self.transfunc.closedFormUpdates(m_qu, S_qu, A_qu, b_qu, self.like.trLen, self.KLdiv.wwLeg)

        # update likelihood parameters
        mu_sp = [[] for _ in range(len(m_sp))]
        cov_sp = [[] for _ in range(len(m_sp))]

        # get predicted means at spike times
        for idx in range(len(m_sp)):
            mu_sp[idx], cov_sp[idx], _ = self.outputMapping(m_sp[idx], S_sp[idx])

        # update output mapping parameters if possible (e.g. for Gaussian likelihood)
        self.outputMapping.closedFormUpdates(self.like, m_sp, S_sp, m_qu, S_qu)

        self.like.closedFormUpdates(mu_sp, cov_sp)

    def forward(self, m_sp, S_sp, m_qu, S_qu, A_qu, b_qu):

        mu_sp = [[] for _ in range(len(m_sp))]
        cov_sp = [[] for _ in range(len(m_sp))]

        # get predicted means at spike times
        for idx in range(len(m_sp)):
            mu_sp[idx], cov_sp[idx], _ = self.outputMapping(m_sp[idx], S_sp[idx])

        # get tensors of predicted means and variances at quadrature nodes
        mu_qu, cov_qu, prior_map = self.outputMapping(m_qu, S_qu)  # R x T x 1 x K and R x T x K x K

        # compute KL divergence between approx post and prior SDE
        fx, ffx, dfdx, prior_trans = self.transfunc(m_qu, S_qu)
        kld = self.KLdiv(fx, ffx, dfdx, m_qu, S_qu, A_qu, b_qu)

        # compute expected log likelihood at sample locations
        ell = torch.zeros(self.like.nTrials, 1)
        for idx in range(self.like.nTrials):
            ell[idx, :] = self.like(mu_sp[idx], cov_sp[idx], idx)

        return ell.sum(), kld, prior_trans, prior_map


class DenseGPSDEmodel(nn.Module):
    """
    This version assumes that observations are sampled densely on the same grid used for inference.
    This will be true e.g. in the Poisson likelihood where observations are binned counts on a chosen grid.
    """

    def __init__(self, nLatent, transfunc, outputMapping, like, nLeg=50):
        super(DenseGPSDEmodel, self).__init__()

        self.outputMapping = outputMapping
        self.transfunc = transfunc
        self.like = like
        self.nLatent = nLatent
        self.KLdiv = KullbackLeibler(self.like.trLen, nLeg=None, dt=self.like.dtstep)
        self.initialiseInitialState()
        torch.set_default_dtype(float_type)

    def initialiseInitialState(self):
        self.initialMean = [torch.zeros(1, self.nLatent).type(float_type) for _ in range(self.like.nTrials)]  # 1 x K
        self.initialCov = [var_init * torch.eye(self.nLatent).type(float_type) for _ in range(self.like.nTrials)]  # K x K

    def collectInferenceResults(self, inference):

        # get time points where we want to evaluate marginals
        m = [[] for _ in range(inference.nTrials)]
        S = [[] for _ in range(inference.nTrials)]
        A = [[] for _ in range(inference.nTrials)]
        b = [[] for _ in range(inference.nTrials)]

        for idx in range(inference.nTrials):
            grid_times = inference.t_grid[idx]
            # get marginals
            m[idx], S[idx] = inference.predict_marginals(idx, grid_times)
            A[idx], b[idx] = inference.predict_conditionalParams(idx, grid_times)

        return (m, S, A, b)  # output tuple

    def closedFormUpdates(self, m, S, A, b):
        self.transfunc.closedFormUpdates(m, S, A, b, self.like.trLen, self.KLdiv.wwLeg)

    def forward(self, m, S, A, b):

        kld = torch.zeros(self.like.nTrials, 1)
        ell = torch.zeros(self.like.nTrials, 1)

        for idx in range(self.like.nTrials):
            mu, cov, _ = self.outputMapping(m[idx], S[idx])
            fx, ffx, dfdx, _ = self.transfunc(m[idx], S[idx])
            kld[idx, :] = self.KLdiv(fx, ffx, dfdx, m[idx], S[idx], A[idx], b[idx])
            ell[idx, :] = self.like(mu, cov, idx)

        prior_trans = self.transfunc.log_prior_distribution()
        prior_map = self.outputMapping.log_prior_distribution()

        return ell.sum(), kld.sum(), prior_trans, prior_map


class PointProcessGPSDEmodel(DenseGPSDEmodel):
    def __init__(self, nLatent, transfunc, outputMapping, like, nLeg=50):
        super(PointProcessGPSDEmodel, self).__init__(nLatent, transfunc, outputMapping, like, nLeg)

        # override other stuff to use quadrature
        self.KLdiv = KullbackLeibler(self.like.trLen, nLeg=nLeg)

    def collectInferenceResults(self, inference):
        # get time points where we want to evaluate marginals
        m_sp = [[] for _ in range(inference.nTrials)]
        S_sp = [[] for _ in range(inference.nTrials)]

        nquad = self.like.xxLeg.size()[1]  # number of quadrature nodes

        # create empty tensors to store predicted means and variances across all trials
        m_qu = torch.zeros(inference.nTrials, nquad, 1, inference.nLatent).type(float_type)
        S_qu = torch.zeros(inference.nTrials, nquad, inference.nLatent, inference.nLatent).type(float_type)
        A_qu = torch.zeros(inference.nTrials, nquad, inference.nLatent, inference.nLatent).type(float_type)
        b_qu = torch.zeros(inference.nTrials, nquad, 1, inference.nLatent).type(float_type)

        for idx in range(inference.nTrials):
            spike_times = self.like.Y[idx]
            quad_times = self.like.xxLeg[idx]
            # get marginals
            m_sp[idx], S_sp[idx] = inference.predict_marginals(idx, spike_times)
            m_qu[idx, :, :, :], S_qu[idx, :, :, :] = inference.predict_marginals(idx, quad_times)
            A_qu[idx, :, :, :], b_qu[idx, :, :, :] = inference.predict_conditionalParams(idx, quad_times)

        return (m_sp, S_sp, m_qu, S_qu, A_qu, b_qu)  # output tuple

    def closedFormUpdates(self, m_sp, S_sp, m_qu, S_qu, A_qu, b_qu):
        self.transfunc.closedFormUpdates(m_qu, S_qu, A_qu, b_qu, self.like.trLen, self.KLdiv.wwLeg)

    def forward(self, m_sp, S_sp, m_qu, S_qu, A_qu, b_qu):
        # make empty lists for storing values
        mu_sp = [[] for _ in range(len(m_sp))]
        cov_sp = [[] for _ in range(len(m_sp))]

        # get predicted means at spike times
        for idx in range(len(m_sp)):
            mu_sp[idx], cov_sp[idx], _ = self.outputMapping(m_sp[idx], S_sp[idx], self.like.spikeID[idx])

        # get tensors of predicted means and variances at quadrature nodes
        mu_qu, cov_qu, prior_map = self.outputMapping(m_qu, S_qu)  # R x T x 1 x K and R x T x K x K

        # compute KL divergence between approx post and prior SDE
        fx, ffx, dfdx, prior_trans = self.transfunc(m_qu, S_qu)
        kld = self.KLdiv(fx, ffx, dfdx, m_qu, S_qu, A_qu, b_qu)

        # compute expected log likelihood
        ell = self.like(mu_sp, cov_sp, mu_qu, cov_qu)

        return ell, kld, prior_trans, prior_map
