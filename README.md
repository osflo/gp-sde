# Stochastic Differential Equation Modelling using Gaussian Processes

This repository contains code to perform variational inference over the path of a latent stochastic differential equation (SDE) and learning of its nonlinear drift function, modelled using a Gaussian Process.

The code is still under active development and is expected to change substantially in the future. We include implementations for Gaussian observation models and linear mappings from latents to observations here, and are planning to release a revised code distribution in the future.

## Getting started
* ./demos contains examples on how to use the code
 * Real Spike trains is a notebook to create a model from data
 * Spike_Neuron_Use_model look at a model and use it to generate new data
 * The two Tutorial notebook are from the main reference and create a model on artificial data
 * The folder Ancient_or_not_used contains diverse notebook created during the project but without conclusion or are outdated

* ./Data contains the data that we used, some functions to get it and a notebook to do some data analysis

## References
The main reference for this work is
* Duncker, L., Bohner, G., Boussard, J., & Sahani, M. (2019). Learning interpretable continuous-time models of latent stochastic dynamical systems. Proceedings of the 36th International Conference on Machine Learning. Long Beach, CA.
