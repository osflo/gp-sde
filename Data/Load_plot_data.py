import numpy as np
import matplotlib.pyplot as plt

def load_neuron_data(file):
    """Load the Neuron data from a file, 
    the file must be a .npz with a file inside representing the ids of the neurons 
    and another with the time of the spikes
    """
    data=np.load(file)
    ids=data['ids']
    times=data['times']
    return ids, times

def roster_plot(ids,times,maxtime=120,markr='|'):
    """Do a roster plot given the data (neuron ids ,times) with the marker markr for times before maxtime"""
    plt.scatter(times[(times<=maxtime)],ids[(times<=maxtime )],marker=markr)
    plt.title('Roster plot')
    plt.xlabel('Times')
    plt.ylabel('Neuron Ids')