import numpy as np
import os
from functools import partial
from bmtk.builder import NetworkBuilder
from bmtk.utils.sim_setup import build_env_bionet
import synapses
import connectors
from connectors import (
    ReciprocalConnector, NormalizedReciprocalRate, UnidirectionConnector,
    OneToOneSequentialConnector, GapJunction,
    syn_const_delay_feng_section_PN, syn_const_delay, rho_2_pr
)

from homogenous_probabilities import homo_edge_probability
##############################################################################
############################## General Settings ##############################

randseed = 1234
rng = np.random.default_rng(randseed)
connectors.rng = rng

network_dir = 'network'
t_sim = 31000.0  # ms
dt = 0.1  # ms

# Network size and dimensions
num_cells = 2000  # 10000
#if we can change the column_height, then 350, 150 is a valid option
column_width, column_height = 275., 250. #keeps height at same value, but network is slightly less dense as a result.
x_start, x_end = - column_width / 2, column_width / 2
y_start, y_end = - column_width / 2, column_width / 2
z_start, z_end = - column_height / 2, column_height / 2

# Distance constraint for all cells
min_conn_dist = 16.0  # um. ~ PN soma diameter
max_conn_dist = 300.0  # or np.inf

# When enabled, a shell of virtual cells will be created around the core cells.
edge_effects = False

##############################################################################
####################### Cell Proportions and Positions #######################

def num_prop(ratio, N):
    """Calculate numbers of total N in proportion to ratio"""
    ratio = np.asarray(ratio)
    p = np.cumsum(np.insert(ratio.ravel(), 0, 0))  # cumulative proportion
    return np.diff(np.round(N / p[-1] * p).astype(int)).reshape(ratio.shape)


# Number of cells in each population.
# Following 85/15 E/I equal on CP-CS and 57% FSI to 43% LTS for Interneurons
# Corticopontine, Corticostriatal,
# Fast Spiking Interneuron, Low Threshold Spiker
num_CP, num_CS, num_FSI, num_LTS = num_prop([42.5, 42.5, 8.5, 6.5], num_cells)
print('Cell numbers: ' + ', '.join(f'{c:s}={n:d}' for c, n in
    zip(['CP', 'CS', 'FSI', 'LTS'], [num_CP, num_CS, num_FSI, num_LTS])))


# Generate random cell positions
# Use poisson-disc sampling to generate positions with minimum distance limit.
use_poiss_disc = True

# Get positions for cells in the core
def samples_in_core(samples):
    core_idx = (samples[:, 0] >= x_start) & (samples[:, 0] <= x_end) \
        & (samples[:, 1] >= y_start) & (samples[:, 1] <= y_end) \
        & (samples[:, 2] >= z_start) & (samples[:, 2] <= z_end)
    return core_idx, samples[core_idx]

# Generate samples in cube with side_length
side_length = max(column_width, column_height)
if edge_effects:
    # Extend side length by 2 * max_conn_dist
    side_length = max(column_width + 2 * max_conn_dist, column_height)

    # Compute the outer shell range. Extend the edge by max_conn_dist.
    shell_x_start, shell_y_start = np.array((x_start, y_start)) - max_conn_dist
    shell_x_end, shell_y_end = np.array((x_end, y_end)) + max_conn_dist

    # Compute the core and shell volume
    core_volume = (x_end - x_start) * (y_end - y_start) * column_height
    shell_volume = (shell_x_end - shell_x_start) * \
        (shell_y_end - shell_y_start) * column_height - core_volume

    # Determine the number of shell cells with the same density
    virt_num_cells = int(round(num_cells * shell_volume / core_volume))


    # Get positions for cells in the shell
    def samples_in_shell(samples):
        shell_idx = (samples[:, 0] >= shell_x_start) & (samples[:, 0] <= shell_x_end) \
            & (samples[:, 1] >= shell_y_start) & (samples[:, 1] <= shell_y_end) \
            & (samples[:, 2] >= z_start) & (samples[:, 2] <= z_end)
        return samples[shell_idx]

# Generate samples in cube [0, 1]^3, then scale it to side_length and center it
def scale_cube(samples):
    return side_length * (samples - 0.5)


if use_poiss_disc:
    from scipy.stats import qmc  # qmc.PoissonDisk new in scipy 1.10.0

    ncand = 30  # number of candidates (related to density of points)
    radius = min_conn_dist / side_length
    engine = qmc.PoissonDisk(d=3, radius=radius, ncandidates=ncand, seed=rng)
    samples = scale_cube(engine.fill_space())

    core_idx, pos_list = samples_in_core(samples)
    print(f"Number of positions in core: {len(pos_list):d}")
    print(f"Number of cells in core: {num_cells:d}")
    if len(pos_list) < num_cells:
        raise ValueError("There are not enough position samples generated.")
    if edge_effects:
        shell_pos_list = samples_in_shell(samples[~core_idx])
        print(f"Number of positions in shell: {len(shell_pos_list):d}")
        print(f"Number of cells in shell: {virt_num_cells:d}")
        if len(shell_pos_list) < virt_num_cells:
            raise ValueError("There are not enough position samples generated.")
else:
    cell_dens = num_cells / (column_width * column_width * column_height)
    num_pos = int(cell_dens * side_length ** 3)
    samples = scale_cube(rng.random((num_pos, 3)))
    num_pos = int(0.1 * num_pos)
    while True:
        core_idx, pos_list = samples_in_core(samples)
        add_samples = len(pos_list) < num_cells
        if edge_effects:
            shell_pos_list = samples_in_shell(samples[~core_idx])
            add_samples = add_samples or len(shell_pos_list) < virt_num_cells
        if add_samples:
            new_samples = scale_cube(rng.random((num_pos, 3)))
            samples = np.concatenate((samples, new_samples), axis=0)
        else:
            break

# Draw desired number of samples from the position list
pos_list = rng.choice(pos_list, num_cells, replace=False)

if edge_effects:
    shell_pos_list = rng.choice(shell_pos_list, virt_num_cells, replace=False)

    virt_num_CP, virt_num_CS, virt_num_FSI, virt_num_LTS = \
        num_prop([num_CP, num_CS, num_FSI, num_LTS], virt_num_cells)


#############################################################################################
################################## UNCORRELATED #############################################
#X:X connectivity: Shell:Bio; ratio of shell to bio connections for convergence of different types of connections.
#PN:PN connectivity: ~ 2:5 (1:2 is too incorrect) ~ 28%
#PN:ITN connectivity:  1:3                        ~ 25%
#ITN:PN connectivity:~ 1:3                        ~ 25%
#ITN:ITN connectivity:~2:5                        ~ 28% 
uncor_PN_percent = 30 #based on comparing proportion in Ziao's
uncor_ITN_percent = 30 #
uncor_num_PN = round((num_CP + num_CS) * uncor_PN_percent / (100 - uncor_PN_percent))
uncor_num_CP, uncor_num_CS= num_prop([num_CP,num_CS], uncor_num_PN)
uncor_num_ITN = round((num_FSI + num_LTS) * uncor_ITN_percent / (100 - uncor_ITN_percent))
uncor_num_FSI, uncor_num_LTS =  num_prop([num_FSI,num_LTS], uncor_num_ITN)

uncor_total = uncor_num_PN + uncor_num_ITN

print(f"Total CP virtual cells : {uncor_num_CP}")
print(f"Total CS virtual cells : {uncor_num_CS}")
print(f"Total FSI virtual cells : {uncor_num_FSI}")
print(f"Total LTS virtual cells : {uncor_num_LTS}")
print(f"Total virtual cells : {uncor_total}")
#If true, a 'shell' of virtual cells will be created around core cells to provide uncorrelated input
uncorrelated = True
# TODO: generate random orientations


##############################################################################
####################### Functions for Building Network #######################

# Dictionary to store NetworkBuilder objects referenced by name
networks = {}

def build_networks(network_definitions: list) -> dict:
    """
    `network_definitions` should be a list of dictionaries, e.g. [{}, {}, ...]
    Keys should include an arbitrary `network_name`, a positions_list (if any),
    and `cells`. `cells` should contain a list of dictionaries, and each
    dictionary should corrospond with any valid input for BMTK
    NetworkBuilder.add_nodes() method. A dictionary of BMTK NetworkBuilder
    objects will be returned, reference by individual network_name."""
    for net_def in network_definitions:
        network_name = net_def['network_name']
        if networks.get(network_name) is None:
            networks[network_name] = NetworkBuilder(network_name)
        pos_list = net_def.get('positions_list')

        # Add cells to the network
        num = 0
        for cell in net_def['cells']:
            num_cells = cell['N']
            extra_kwargs = {}
            if pos_list is not None:
                extra_kwargs['positions'] = pos_list[num:num + num_cells]
                num += num_cells

            cell = {k: v for k, v in cell.items() if v is not None}
            extra_kwargs = {k: v for k, v in extra_kwargs.items()
                            if v is not None}
            networks[network_name].add_nodes(**cell, **extra_kwargs)

    return networks


def build_edges(networks, edge_definitions, edge_params,
                edge_add_properties, syn):
    """
    Builds the edges for each network given a set of 'edge_definitions',
    examples shown later in the code
    """
    for edge in edge_definitions:
        network_name = edge['network']
        net = networks[network_name]
        # edge arguments
        print("Adding edge: " + edge['param'])
        edge_params_val = edge_params[edge['param']].copy()
        # get synapse template file
        dynamics_file = edge_params_val.get('dynamics_params')
        model_template = syn[dynamics_file]['level_of_detail']
        # get source and target nodes
        edge_src_trg = edge.get('edge')
        if edge_src_trg:
            edge_src_trg = edge_src_trg.copy()
            src_net = edge_src_trg.pop('source_network', network_name)
            trg_net = edge_src_trg.pop('target_network', network_name)
            source = networks[src_net].nodes(**edge_src_trg.get('source', {}))
            target = networks[trg_net].nodes(**edge_src_trg.get('target', {}))
            edge_params_val.update({'source': source, 'target': target})
        # use connector class
        connector_class = edge_params_val.pop('connector_class', None)
        if connector_class is not None:
            # create a connector object
            connector_params = edge_params_val.pop('connector_params', {})
            connector = connector_class(**connector_params)
            # keep object reference in the dictionary
            edge_params[edge['param']]['connector_object'] = connector
            if edge_src_trg:
                connector.setup_nodes(source=source, target=target)
            edge_params_val.update(connector.edge_params())
        conn = net.add_edges(model_template=model_template, **edge_params_val)

        edge_properties = edge.get('add_properties')
        if edge_properties:
            edge_properties_val = edge_add_properties[edge_properties].copy()
            if connector_class is not None:
                # pass connector object to the rule for edge properties
                edge_properties_val['rule'] = partial(
                    edge_properties_val['rule'], connector=connector)
            conn.add_properties(**edge_properties_val)


def get_connector(param):
    """Get connector object stored in edge_params"""
    edge_params_val = edge_params[param]
    if 'connector_object' in edge_params_val:
        return edge_params_val['connector_object']
    else:
        raise ValueError("No connector used in '%s'" % param)


def save_networks(networks, network_dir):
    """Build and save network"""
    # Remove the existing network_dir directory
    if os.path.isdir(network_dir):
        for f in os.listdir(network_dir):
            os.remove(os.path.join(network_dir, f))

    # Run through each network and save their nodes/edges
    for network_name, network in networks.items():
        print('Building ' + network_name)
        network.build()
        network.save_nodes(output_dir=network_dir)
        network.save_edges(output_dir=network_dir)


##############################################################################
############################ Network Definitions #############################

network_definitions = [
    {   # Start Layer 5
        'network_name': 'cortex',
        'positions_list': pos_list,
        'cells': [
            {   # CP
                'N': num_CP,
                'pop_name': 'CP',
                'rotation_angle_zaxis': None,
                'rotation_angle_yaxis': None,
                'model_type': 'biophysical',
                'model_template': 'hoc:CP_Cell',
                'morphology': 'blank.swc'
            },
            {   # CS
                'N': num_CS,
                'pop_name': 'CS',
                'rotation_angle_zaxis': None,
                'rotation_angle_yaxis': None,
                'model_type': 'biophysical',
                'model_template': 'hoc:CS_Cell',
                'morphology': 'blank.swc'
            },
            {   # FSI
                'N': num_FSI,
                'pop_name': 'FSI',
                'rotation_angle_zaxis': None,
                'rotation_angle_yaxis': None,
                'model_type': 'biophysical',
                'model_template': 'hoc:FSI_Cell',
                'morphology': 'blank.swc'
            },
            {   # LTS
                'N': num_LTS,
                'pop_name': 'LTS',
                'rotation_angle_zaxis': None,
                'rotation_angle_yaxis': None,
                'model_type': 'biophysical',
                'model_template': 'hoc:LTS_Cell',
                'morphology': 'blank.swc'
            }
        ]
    },  # End Layer 5
    {   # Extrinsic Thalamic Inputs
        'network_name': 'thalamus',
        'positions_list': None,
        'cells': [
            {   # Virtual Cells
                'N': num_CP + num_CS,
                'pop_name': 'thal',
                'potential': 'exc',
                'model_type': 'virtual'
            }
        ]
    },
    {   # Extrinsic Baseline Inputs
        'network_name': 'baseline',
        'positions_list': None,
        'cells': [
            {   # Virtual Cells
                'N': num_cells,
                'pop_name': 'base',
                'potential': 'exc',
                'model_type': 'virtual'
            }
        ]
    }
]

##############################################################################
################################ UNCORRELATED EFFECTS ########################
if uncorrelated:
    # This network should contain all the same properties as the original
    # network, except the cell should be virtual. For connectivity, you should
    # make a different connection rule because they will connect slightly differently
    uncorrelated_network = [
        {
            'network_name' : 'uncorrelated',
            'cells': [
                { # CP
                 'N': uncor_num_CP,
                 'pop_name': 'CP',
                 'model_type': 'virtual'   
                },
                { # CS
                 'N': uncor_num_CS,
                 'pop_name': 'CS',
                 'model_type': 'virtual'   
                },
                { # FSI
                 'N': uncor_num_FSI,
                 'pop_name': 'FSI',
                 'model_type': 'virtual'   
                },
                { # LTS
                 'N': uncor_num_LTS,
                 'pop_name': 'LTS',
                 'model_type': 'virtual'   
                }
            ]
        }
    ]
    
    # Add the uncorrelated to our network definitions
    network_definitions.extend(uncorrelated_network)

############################## END UNCORRELATED ##############################
##############################################################################

##############################################################################
################################ EDGE EFFECTS ################################

if edge_effects:
    # This network should contain all the same properties as the original
    # network, except the cell should be virtual. For connectivity, you should
    # name the cells the same as the original network because connection rules
    # defined later will require it
    shell_network = [
        {   # Start Layer 5
            'network_name': 'shell',
            'positions_list': shell_pos_list,
            'cells': [
                {   # CP
                    'N': virt_num_CP,
                    'pop_name': 'CP',
                    'model_type': 'virtual'
                },
                {   # CS
                    'N': virt_num_CS,
                    'pop_name': 'CS',
                    'model_type': 'virtual'
                },
                {   # FSI
                    'N': virt_num_FSI,
                    'pop_name': 'FSI',
                    'model_type': 'virtual'
                },
                {   # LTS
                    'N': virt_num_LTS,
                    'pop_name': 'LTS',
                    'model_type': 'virtual'
                }
            ]
        }  # End Layer 5
    ]

    # Add the shell to our network definitions
    network_definitions.extend(shell_network)

############################## END EDGE EFFECTS ##############################
##############################################################################

# Build and save our NetworkBuilder dictionary
networks = build_networks(network_definitions)


##########################################################################
#############################  BUILD EDGES  ##############################

# Whole reason for restructuring network building lies here, by separating out
# the source and target params from the remaining parameters in
# NetworkBuilder.add_edges() function we can reuse connectivity rules for the
# virtual shell or elsewhere
# [
#  {
#   'network': 'network_name',  # Name of the network to which edges are added
#   'edge': {
#       'source': {},  # dictionary of properties of desired population
#       'target': {},
#       'source_network': 'network_name1'  # network name for the population
#       'target_network': 'network_name2'  # if different from network_name
#       },  # source and target are required
#       # source_network and target_network are optional
#   'param': 'name_of_edge_parameters',  # to be coupled with when add_edges()
#   'add_properties': 'prop_name'  # name of edge_add_properties for additional
#       # connection properties, like delay
#   }
# ]

edge_definitions = [
    {   # CP -> CP Reciprocal
        'network': 'cortex',
        'edge': {
            'source': {'pop_name': ['CP']},
            'target': {'pop_name': ['CP']}
        },
        'param': 'CP2CP',
        'add_properties': 'syn_const_delay_E2E'
    },
    {   # CS -> CS Reciprocal
        'network': 'cortex',
        'edge': {
            'source': {'pop_name': ['CS']},
            'target': {'pop_name': ['CS']}
        },
        'param': 'CS2CS',
        'add_properties': 'syn_const_delay_E2E'
    },
    {   # CP -> CS Unidirectional
        'network': 'cortex',
        'edge': {
            'source': {'pop_name': ['CP']},
            'target': {'pop_name': ['CS']}
        },
        'param': 'CP2CS',
        'add_properties': 'syn_const_delay_E2E'
    },
    {   # CS -> CP Unidirectional
        'network': 'cortex',
        'edge': {
            'source': {'pop_name': ['CS']},
            'target': {'pop_name': ['CP']}
        },
        'param': 'CS2CP',
        'add_properties': 'syn_const_delay_E2E'
    },
    {   # FSI -> FSI Reciprocal
        'network': 'cortex',
        'edge': {
            'source': {'pop_name': ['FSI']},
            'target': {'pop_name': ['FSI']}
        },
        'param': 'FSI2FSI',
        'add_properties': 'syn_const_delay_FSI'
    },
    {   # LTS -> LTS Unidirectional
        'network': 'cortex',
        'edge': {
            'source': {'pop_name': ['LTS']},
            'target': {'pop_name': ['LTS']}
        },
        'param': 'LTS2LTS',
        'add_properties': 'syn_const_delay_LTS'
    },
    {   # FSI -> LTS forward
        'network': 'cortex',
        'edge': {
            'source': {'pop_name': ['FSI']},
            'target': {'pop_name': ['LTS']}
        },
        'param': 'FSI2LTS',
        'add_properties': 'syn_const_delay_FSI'
    },
    {   # FSI <- LTS backward
        'network': 'cortex',
        'edge': {
            'source': {'pop_name': ['LTS']},
            'target': {'pop_name': ['FSI']}
        },
        'param': 'LTS2FSI',
        'add_properties': 'syn_const_delay_LTS'
    },
    {   # CP -> FSI forward
        'network': 'cortex',
        'edge': {
            'source': {'pop_name': ['CP']},
            'target': {'pop_name': ['FSI']}
        },
        'param': 'CP2FSI',
        'add_properties': 'syn_const_delay_E2I'
    },
    {   # CP <- FSI backward
        'network': 'cortex',
        'edge': {
            'source': {'pop_name': ['FSI']},
            'target': {'pop_name': ['CP']}
        },
        'param': 'FSI2CP',
        'add_properties': 'syn_const_delay_FSI'
    },
    {   # CS -> FSI forward
        'network': 'cortex',
        'edge': {
            'source': {'pop_name': ['CS']},
            'target': {'pop_name': ['FSI']}
        },
        'param': 'CS2FSI',
        'add_properties': 'syn_const_delay_E2I'
    },
    {   # CS <- FSI backward
        'network': 'cortex',
        'edge': {
            'source': {'pop_name': ['FSI']},
            'target': {'pop_name': ['CS']}
        },
        'param': 'FSI2CS',
        'add_properties': 'syn_const_delay_FSI'
    },
    {   # CP -> LTS forward
        'network': 'cortex',
        'edge': {
            'source': {'pop_name': ['CP']},
            'target': {'pop_name': ['LTS']}
        },
        'param': 'CP2LTS',
        'add_properties': 'syn_const_delay_E2I'
    },
    {   # CP <- LTS backward
        'network': 'cortex',
        'edge': {
            'source': {'pop_name': ['LTS']},
            'target': {'pop_name': ['CP']}
        },
        'param': 'LTS2CP',
        'add_properties': 'syn_const_delay_LTS'
    },
    {   # CS -> LTS forward
        'network': 'cortex',
        'edge': {
            'source': {'pop_name': ['CS']},
            'target': {'pop_name': ['LTS']}
        },
        'param': 'CS2LTS',
        'add_properties': 'syn_const_delay_E2I'
    },
    {   # CS <- LTS backward
        'network': 'cortex',
        'edge': {
            'source': {'pop_name': ['LTS']},
            'target': {'pop_name': ['CS']}
        },
        'param': 'LTS2CS',
        'add_properties': 'syn_const_delay_LTS'
    },
        ################### THALAMIC INPUT ###################
    {   # Thalamus Excitation to CP
        'network': 'cortex',
        'edge': {
            'source_network': 'thalamus',
            'source': {},
            'target': {'pop_name': ['CP']}
        },
        'param': 'Thal2CP'
    },
    {   # Thalamus Excitation to CS
        'network': 'cortex',
        'edge': {
            'source_network': 'thalamus',
            'source': {},
            'target': {'pop_name': ['CS']}
        },
        'param': 'Thal2CS'
    },
        ################### Baseline INPUT ###################
    {   # Excitation to CP
        'network': 'cortex',
        'edge': {
            'source_network': 'baseline',
            'source': {},
            'target': {'pop_name': ['CP']}
        },
        'param': 'Base2CP'
    },
    {   # Excitation to CS
        'network': 'cortex',
        'edge': {
            'source_network': 'baseline',
            'source': {},
            'target': {'pop_name': ['CS']}
        },
        'param': 'Base2CS'
    },
    {   # Excitation to FSI
        'network': 'cortex',
        'edge': {
            'source_network': 'baseline',
            'source': {},
            'target': {'pop_name': ['FSI']}
        },
        'param': 'Base2FSI'
    },
    {   # Excitation to LTS
        'network': 'cortex',
        'edge': {
            'source_network': 'baseline',
            'source': {},
            'target': {'pop_name': ['LTS']}
        },
        'param': 'Base2LTS'
    },
     ################### UNCORRELATED INPUT ###################
    {   #uncorrelated CP -> cortex CP Unidirectional
        'network': 'uncorrelated',
        'edge': {
            'source': {'pop_name': ['CP']},
            'target': {'pop_name': ['CP']},
            'source_network': 'uncorrelated',
            'target_network': 'cortex'
        },
        'param': 'UNCOR_CP2CP',
        'add_properties': 'syn_const_delay_E2E'
    },
    {   #uncorrelated CS -> cortex CS Unidirectional
        'network': 'uncorrelated',
        'edge': {
            'source': {'pop_name': ['CS']},
            'target': {'pop_name': ['CS']},
            'source_network': 'uncorrelated',
            'target_network': 'cortex'
        },
        'param': 'UNCOR_CS2CS',
        'add_properties': 'syn_const_delay_E2E'
    },
    {   #uncorrelated CP -> cortex CS Unidirectional
        'network': 'uncorrelated',
        'edge': {
            'source': {'pop_name': ['CP']},
            'target': {'pop_name': ['CS']},
            'source_network': 'uncorrelated',
            'target_network': 'cortex'
        },
        'param': 'UNCOR_CP2CS',
        'add_properties': 'syn_const_delay_E2E'
    },
    {   #uncorrelated CS -> cortex CP Unidirectional
        'network': 'uncorrelated',
        'edge': {
            'source': {'pop_name': ['CS']},
            'target': {'pop_name': ['CP']},
            'source_network': 'uncorrelated',
            'target_network': 'cortex'
        },
        'param': 'UNCOR_CS2CP',
        'add_properties': 'syn_const_delay_E2E'
    },
    {   #uncorrelated FSI -> cortex FSI Unidirectional
        'network': 'uncorrelated',
        'edge': {
            'source': {'pop_name': ['FSI']},
            'target': {'pop_name': ['FSI']},
            'source_network': 'uncorrelated',
            'target_network': 'cortex'
        },
        'param': 'UNCOR_FSI2FSI',
        'add_properties': 'syn_const_delay_FSI'
    },
    {   #uncorrelated LTS -> cortex LTS Unidirectional
        'network': 'uncorrelated',
        'edge': {
            'source': {'pop_name': ['LTS']},
            'target': {'pop_name': ['LTS']},
            'source_network': 'uncorrelated',
            'target_network': 'cortex'
        },
        'param': 'UNCOR_LTS2LTS',
        'add_properties': 'syn_const_delay_LTS'
    },
    {   #uncorrelated FSI -> LTS Unidirectional
        'network': 'uncorrelated',
        'edge': {
            'source': {'pop_name': ['FSI']},
            'target': {'pop_name': ['LTS']},
            'source_network': 'uncorrelated',
            'target_network': 'cortex'
        },
        'param': 'UNCOR_FSI2LTS',
        'add_properties': 'syn_const_delay_FSI'
    },
    {   #uncorrelated LTS -> cortex FSI Unidirectional
        'network': 'uncorrelated',
        'edge': {
            'source': {'pop_name': ['LTS']},
            'target': {'pop_name': ['FSI']},
            'source_network': 'uncorrelated',
            'target_network': 'cortex'
        },
        'param': 'UNCOR_LTS2FSI',
        'add_properties': 'syn_const_delay_LTS'
    },
    {   #uncorrelated CP -> cortex FSI Unidirectional
        'network': 'uncorrelated',
        'edge': {
            'source': {'pop_name': ['CP']},
            'target': {'pop_name': ['FSI']},
            'source_network': 'uncorrelated',
            'target_network': 'cortex'
        },
        'param': 'UNCOR_CP2FSI',
        'add_properties': 'syn_const_delay_E2I'
    },
    {   #uncorrelated FSI -> cortex CP Unidirectional
        'network': 'uncorrelated',
        'edge': {
            'source': {'pop_name': ['FSI']},
            'target': {'pop_name': ['CP']},
            'source_network': 'uncorrelated',
            'target_network': 'cortex'
        },
        'param': 'UNCOR_FSI2CP',
        'add_properties': 'syn_const_delay_FSI'
    },
    {   #uncorrelated CS -> cortex FSI Unidirectional
        'network': 'uncorrelated',
        'edge': {
            'source': {'pop_name': ['CS']},
            'target': {'pop_name': ['FSI']},
            'source_network': 'uncorrelated',
            'target_network': 'cortex'
        },
        'param': 'UNCOR_CS2FSI',
        'add_properties': 'syn_const_delay_E2I'
    },
    {   #uncorrelated FSI -> cortex CS Unidirectional
        'network': 'uncorrelated',
        'edge': {
            'source': {'pop_name': ['FSI']},
            'target': {'pop_name': ['CS']},
            'source_network': 'uncorrelated',
            'target_network': 'cortex'
        },
        'param': 'UNCOR_FSI2CS',
        'add_properties': 'syn_const_delay_FSI'
    },
    {   #uncorrelated CP -> cortex LTS Unidirectional
        'network': 'uncorrelated',
        'edge': {
            'source': {'pop_name': ['CP']},
            'target': {'pop_name': ['LTS']},
            'source_network': 'uncorrelated',
            'target_network': 'cortex'
        },
        'param': 'UNCOR_CP2LTS',
        'add_properties': 'syn_const_delay_E2I'
    },
    {   #uncorrelated LTS -> cortex CP Unidirectional
        'network': 'uncorrelated',
        'edge': {
            'source': {'pop_name': ['LTS']},
            'target': {'pop_name': ['CP']},
            'source_network': 'uncorrelated',
            'target_network': 'cortex'
        },
        'param': 'UNCOR_LTS2CP',
        'add_properties': 'syn_const_delay_LTS'
    },
    {   #uncorrelated CS -> cortex LTS Unidirectional
        'network': 'uncorrelated',
        'edge': {
            'source': {'pop_name': ['CS']},
            'target': {'pop_name': ['LTS']},
            'source_network': 'uncorrelated',
            'target_network': 'cortex'
        },
        'param': 'UNCOR_CS2LTS',
        'add_properties': 'syn_const_delay_E2I'
    },
    {   #uncorrelated LTS -> cortex CS Unidirectional
        'network': 'uncorrelated',
        'edge': {
            'source': {'pop_name': ['LTS']},
            'target': {'pop_name': ['CS']},
            'source_network': 'uncorrelated',
            'target_network': 'cortex'
        },
        'param': 'UNCOR_LTS2CS',
        'add_properties': 'syn_const_delay_LTS'
    },
]

# edge_params should contain additional parameters to be added to add_edges().
# The following parameters for random synapse placement are not necessary in
# edge_params if afferent_section_id and afferent_section_pos are specified.
# distance_range: place synapse within distance range [dmin, dmax] from soma.
# target_sections: place synapse within the given sections in a list.
# afferent_section_id must be specified here even though it will be overwritten
# by add_properties(), since there could be potential error due to the dtype
# being forced to be converted to float if values are not specified in the
# corresponding column in the edge csv file.


# number of every type of cell in the distant dependent model
dist_num_cells = 10000
dist_num_CP, dist_num_CS, dist_num_FSI, dist_num_LTS = num_prop([42.5, 42.5, 8.5, 6.5], dist_num_cells)

#All connection numbers were obtained from the V1_D_build.out file.

edge_params = {
    'CP2CP': {
        'connector_class': ReciprocalConnector,
        'connector_params': {
            'p0': homo_edge_probability(uni_connections=447042,num_cell_B=dist_num_CP, homo_num_cell_A=num_CP, 
                                              rec_connections=95439, connect_to_same_type=True),
            'pr': NormalizedReciprocalRate(NRR=3)
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 1,
        'afferent_section_pos': 0.4,
        'dynamics_params': 'CP2CP.json'
    },
    'CS2CS': {
        'connector_class': ReciprocalConnector,
        'connector_params': {
            'p0': homo_edge_probability(uni_connections=187667,num_cell_B=dist_num_CS, homo_num_cell_A=num_CS, 
                                               rec_connections=13118, connect_to_same_type=True),
            'pr': NormalizedReciprocalRate(NRR=3)
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 1,
        'afferent_section_pos': 0.4,
        'dynamics_params': 'CS2CS.json'
    },
    'CP2CS': {
        'connector_class': ReciprocalConnector,
        'connector_params': {
            #CP->CS
            'p0': homo_edge_probability(uni_connections=34034,num_cell_B=dist_num_CS, homo_num_cell_A=num_CP),
            #CS->CP
            'p1': homo_edge_probability(uni_connections=303996,num_cell_B=dist_num_CP, homo_num_cell_A=num_CS),
            'pr': NormalizedReciprocalRate(NRR=3)
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 1,
        'afferent_section_pos': 0.4,
        'dynamics_params': 'CP2CS.json'
    },
    'CS2CP': {
        'connector_class': get_connector,
        'connector_params': {'param': 'CP2CS'},
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 1,
        'afferent_section_pos': 0.4,
        'dynamics_params': 'CS2CP.json'
    },
    'FSI2FSI': {
        'connector_class': ReciprocalConnector,
        'connector_params': {
            'p0': homo_edge_probability(uni_connections=39246,num_cell_B=dist_num_FSI, homo_num_cell_A=num_FSI, 
                                               rec_connections=27866, connect_to_same_type=True),
            'pr': NormalizedReciprocalRate(NRR=3)
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 0,  # soma
        'afferent_section_pos': 0.5,
        'dynamics_params': 'FSI2FSI.json'
    },
    'LTS2LTS': {
        'connector_class': ReciprocalConnector,
        'connector_params': {
            'p0': homo_edge_probability(uni_connections=7206,num_cell_B=dist_num_LTS, homo_num_cell_A=num_LTS, 
                                               rec_connections=753, connect_to_same_type=True),
            'pr': NormalizedReciprocalRate(NRR=3)
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 0,  # soma
        'afferent_section_pos': 0.5,
        'dynamics_params': 'LTS2LTS.json'
    },
    'FSI2LTS': {
        'connector_class': ReciprocalConnector,
        'connector_params': {
            #FSI2LTS
            'p0': homo_edge_probability(uni_connections=14573,num_cell_B=dist_num_LTS , homo_num_cell_A=num_FSI),
            #LTS2FSI
            'p1': homo_edge_probability(uni_connections=30280,num_cell_B=dist_num_FSI , homo_num_cell_A=num_LTS),
            'pr': NormalizedReciprocalRate(NRR=3)
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 0,  # soma
        'afferent_section_pos': 0.5,
        'dynamics_params': 'FSI2LTS.json'
    },
    'LTS2FSI': {
        'connector_class': get_connector,
        'connector_params': {'param': 'FSI2LTS'},
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 0,  # soma
        'afferent_section_pos': 0.5,
        'dynamics_params': 'LTS2FSI.json'
    },
    'CP2FSI': {
        'connector_class': ReciprocalConnector,
        'connector_params': {
            #CP2FSI
            'p0': homo_edge_probability(uni_connections=84035,num_cell_B=dist_num_FSI, homo_num_cell_A=num_CP),
            #FSI2CP
            'p1': homo_edge_probability(uni_connections=191158,num_cell_B=dist_num_CP, homo_num_cell_A=num_FSI),
            'pr': NormalizedReciprocalRate(NRR=3)
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 1,  # dend
        'afferent_section_pos': 0.5,
        'dynamics_params': 'CP2FSI.json'
    },
    'FSI2CP': {
        'connector_class': get_connector,
        'connector_params': {'param': 'CP2FSI'},
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 0,  # soma
        'afferent_section_pos': 0.5,
        'dynamics_params': 'FSI2CP.json'
    },
    'CS2FSI': {
        'connector_class': ReciprocalConnector,
        'connector_params': {
            #CS2FSI
            'p0': homo_edge_probability(uni_connections=103106,num_cell_B=dist_num_FSI, homo_num_cell_A=num_CS),
            #FSI2CS
            'p1': homo_edge_probability(uni_connections=168611,num_cell_B=dist_num_CS, homo_num_cell_A=num_FSI),
            'pr': NormalizedReciprocalRate(NRR=3)
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 1,  # dend
        'afferent_section_pos': 0.5,
        'dynamics_params': 'CS2FSI.json'
    },
    'FSI2CS': {
        'connector_class': get_connector,
        'connector_params': {'param': 'CS2FSI'},
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 0,  # soma
        'afferent_section_pos': 0.5,
        'dynamics_params': 'FSI2CS.json'
    },
    'CP2LTS': {
        'connector_class': ReciprocalConnector,
        'connector_params': {
            #CP2LTS
            'p0': homo_edge_probability(uni_connections=84025,num_cell_B=dist_num_LTS, homo_num_cell_A=num_CP),
            #LTS2CP
            'p1': homo_edge_probability(uni_connections=179164,num_cell_B=dist_num_CP, homo_num_cell_A=num_LTS),
            'pr': NormalizedReciprocalRate(NRR=3)
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 1,  # dend
        'afferent_section_pos': 0.5,
        'dynamics_params': 'CP2LTS.json'
    },
    'LTS2CP': {
        'connector_class': get_connector,
        'connector_params': {'param': 'CP2LTS'},
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 2,
        'afferent_section_pos': 0.8,  # end of apic
        'dynamics_params': 'LTS2CP.json'
    },
    'CS2LTS': {
        'connector_class': ReciprocalConnector,
        'connector_params': {
            #CS2LTS
            'p0': homo_edge_probability(uni_connections=93748,num_cell_B=dist_num_LTS, homo_num_cell_A=num_CS),
            #LTS2CS
            'p1': homo_edge_probability(uni_connections=44384,num_cell_B=dist_num_CS, homo_num_cell_A=num_LTS),
            'pr': NormalizedReciprocalRate(NRR=3)
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 1,  # dend
        'afferent_section_pos': 0.5,
        'dynamics_params': 'CS2LTS.json'
    },
    'LTS2CS': {
        'connector_class': get_connector,
        'connector_params': {'param': 'CS2LTS'},
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 2,
        'afferent_section_pos': 0.8,  # end of apic
        'dynamics_params': 'LTS2CS.json'
    },
    'Thal2CP': {
        'connector_class': OneToOneSequentialConnector,
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.3,
        'sigma_upper_bound': 3.,
        'delay': 0.0,
        'afferent_section_id': 2,
        'afferent_section_pos': 0.8,  # end of apic
        'dynamics_params': 'Thal2CP.json'
    },
    'Thal2CS': {
        'connector_class': get_connector,
        'connector_params': {'param': 'Thal2CP'},
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.3,
        'sigma_upper_bound': 3.,
        'delay': 0.0,
        'afferent_section_id': 2,
        'afferent_section_pos': 0.8,  # end of apic
        'dynamics_params': 'Thal2CS.json'
    },
    'Base2CP': {
        'connector_class': OneToOneSequentialConnector,
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.3,
        'sigma_upper_bound': 3.,
        'delay': 0.0,
        'afferent_section_id': 2,
        'afferent_section_pos': 0.8,  # end of apic
        'dynamics_params': 'Base2CP.json'
    },
    'Base2CS': {
        'connector_class': get_connector,
        'connector_params': {'param': 'Base2CP'},
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.3,
        'sigma_upper_bound': 3.,
        'delay': 0.0,
        'afferent_section_id': 2,
        'afferent_section_pos': 0.8,  # end of apic
        'dynamics_params': 'Base2CS.json'
    },
    'Base2FSI': {
        'connector_class': get_connector,
        'connector_params': {'param': 'Base2CP'},
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.6,
        'sigma_upper_bound': 3.,
        'delay': 0.0,
        'afferent_section_id': 1,  # dend
        'afferent_section_pos': 0.5,
        'dynamics_params': 'Base2FSI.json'
    },
    'Base2LTS': {
        'connector_class': get_connector,
        'connector_params': {'param': 'Base2CP'},
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.6,
        'sigma_upper_bound': 3.,
        'delay': 0.0,
        'afferent_section_id': 1,  # dend
        'afferent_section_pos': 0.5,
        'dynamics_params': 'Base2LTS.json'
    },
    ##########################################################
    ################### UNCORRELATED INPUT ###################
    'UNCOR_CP2CP': {
        'connector_class': UnidirectionConnector,
        'connector_params': {
            'p': homo_edge_probability(uni_connections=218265,num_cell_B=dist_num_CP, homo_num_cell_A=uncor_num_CP),
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 1,
        'afferent_section_pos': 0.4,
        'dynamics_params': 'CP2CP.json'
    },
    'UNCOR_CS2CS': {
        'connector_class': UnidirectionConnector,
        'connector_params': {
            'p': homo_edge_probability(uni_connections=82285,num_cell_B=dist_num_CS, homo_num_cell_A=uncor_num_CS),
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 1,
        'afferent_section_pos': 0.4,
        'dynamics_params': 'CS2CS.json'
    },
    'UNCOR_CP2CS': {
        'connector_class': UnidirectionConnector,
        'connector_params': {
            'p': homo_edge_probability(uni_connections=13836,num_cell_B=dist_num_CS, homo_num_cell_A=uncor_num_CP),
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 1,
        'afferent_section_pos': 0.4,
        'dynamics_params': 'CP2CS.json'
    },
    'UNCOR_CS2CP': {
        'connector_class': UnidirectionConnector,
        'connector_params': {
            'p': homo_edge_probability(uni_connections=120877,num_cell_B=dist_num_CP, homo_num_cell_A=uncor_num_CS),
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 1,
        'afferent_section_pos': 0.4,
        'dynamics_params': 'CS2CP.json'
    },
    'UNCOR_FSI2FSI': {
        'connector_class': UnidirectionConnector,
        'connector_params': {
            'p': homo_edge_probability(uni_connections=27265,num_cell_B=dist_num_FSI, homo_num_cell_A=uncor_num_FSI),
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 0,  # soma
        'afferent_section_pos': 0.5,
        'dynamics_params': 'FSI2FSI.json'
    },
    'UNCOR_LTS2LTS': {
        'connector_class': UnidirectionConnector,
        'connector_params': {
            'p': homo_edge_probability(uni_connections=2899,num_cell_B=dist_num_LTS, homo_num_cell_A=uncor_num_LTS),
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 0,  # soma
        'afferent_section_pos': 0.5,
        'dynamics_params': 'LTS2LTS.json'
    },
    'UNCOR_FSI2LTS': {
        'connector_class': UnidirectionConnector,
        'connector_params': {
            'p': homo_edge_probability(uni_connections=5677,num_cell_B=dist_num_LTS, homo_num_cell_A=uncor_num_FSI),
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 0,  # soma
        'afferent_section_pos': 0.5,
        'dynamics_params': 'FSI2LTS.json'
    },
    'UNCOR_LTS2FSI': {
        'connector_class': UnidirectionConnector,
        'connector_params': {
            'p': homo_edge_probability(uni_connections=11499,num_cell_B=dist_num_FSI, homo_num_cell_A=uncor_num_LTS),
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 0,  # soma
        'afferent_section_pos': 0.5,
        'dynamics_params': 'LTS2FSI.json'
    },
    'UNCOR_CP2FSI': {
        'connector_class': UnidirectionConnector,
        'connector_params': {
            'p': homo_edge_probability(uni_connections=27253,num_cell_B=dist_num_FSI, homo_num_cell_A=uncor_num_CP),
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 1,  # dend
        'afferent_section_pos': 0.5,
        'dynamics_params': 'CP2FSI.json'
    },
    'UNCOR_FSI2CP': {
        'connector_class': UnidirectionConnector,
        'connector_params': {
            'p': homo_edge_probability(uni_connections=59321,num_cell_B=dist_num_CP, homo_num_cell_A=uncor_num_FSI),
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 0,  # soma
        'afferent_section_pos': 0.5,
        'dynamics_params': 'FSI2CP.json'
    },
    'UNCOR_CS2FSI': {
        'connector_class': UnidirectionConnector,
        'connector_params': {
            'p': homo_edge_probability(uni_connections=32821,num_cell_B=dist_num_FSI, homo_num_cell_A=uncor_num_CS),
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 1,  # dend
        'afferent_section_pos': 0.5,
        'dynamics_params': 'CS2FSI.json'
    },
    'UNCOR_FSI2CS': {
        'connector_class': UnidirectionConnector,
        'connector_params': {
            'p': homo_edge_probability(uni_connections=53425,num_cell_B=dist_num_CS, homo_num_cell_A=uncor_num_FSI),
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 0,  # soma
        'afferent_section_pos': 0.5,
        'dynamics_params': 'FSI2CS.json'
    },
    'UNCOR_CP2LTS': {
        'connector_class': UnidirectionConnector,
        'connector_params': {
            'p': homo_edge_probability(uni_connections=25691,num_cell_B=dist_num_LTS, homo_num_cell_A=uncor_num_CP),
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 1,  # dend
        'afferent_section_pos': 0.5,
        'dynamics_params': 'CP2LTS.json'
    },
    'UNCOR_LTS2CP': {
        'connector_class': UnidirectionConnector,
        'connector_params': {
            'p': homo_edge_probability(uni_connections=51324,num_cell_B=dist_num_CP, homo_num_cell_A=uncor_num_LTS),
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 2,
        'afferent_section_pos': 0.8,  # end of apic
        'dynamics_params': 'LTS2CP.json'
    },
    'UNCOR_CS2LTS': {
        'connector_class': UnidirectionConnector,
        'connector_params': {
            'p': homo_edge_probability(uni_connections=28674,num_cell_B=dist_num_LTS, homo_num_cell_A=uncor_num_CS),
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 1,  # dend
        'afferent_section_pos': 0.5,
        'dynamics_params': 'CS2LTS.json'
    },
    'UNCOR_LTS2CS': {
        'connector_class': UnidirectionConnector,
        'connector_params': {
            'p': homo_edge_probability(uni_connections=13068,num_cell_B=dist_num_CS, homo_num_cell_A=uncor_num_LTS),
            },
        'weight_function': 'lognormal_weight',
        'syn_weight': 1.,
        'weight_sigma': 0.8,
        'sigma_upper_bound': 3.,
        'afferent_section_id': 2,
        'afferent_section_pos': 0.8,  # end of apic
        'dynamics_params': 'LTS2CS.json'
    }
}  # edges referenced by name

# Will be called by conn.add_properties() for the associated connection

edge_add_properties = {
    'syn_const_delay_E2E': {
        'names': ['delay', 'afferent_section_id', 'afferent_section_pos'],
        'rule': syn_const_delay_feng_section_PN,
        'rule_params': {
            'p': 0.9, 'sec_id': (1, 2), 'sec_x': (0.4, 0.6),
            'min_delay': 1.6, 'delay_bound': (0.2, 2.4)
        },
        'dtypes': [float, np.uint16, float]
    },
    'syn_const_delay_E2I': {
        'names': 'delay',
        'rule': syn_const_delay,
        'rule_params': {'min_delay': 1.2},
        'dtypes': float
    },
    'syn_const_delay_FSI': {
        'names': 'delay',
        'rule': syn_const_delay,
        'rule_params': {'min_delay': 0.8},
        'dtypes': float
    },
    'syn_const_delay_LTS': {
        'names': 'delay',
        'rule': syn_const_delay,
        'rule_params': {'min_delay': 1.1},
        'dtypes': float
    }
}


# Load synapse dictionaries
# See synapses.py - loads each json's in components/synaptic_models/synapses_STP
# into a dictionary so the properties can be referenced in the files,
# e.g., syn['file.json'].get('property')
syn_dir = 'components/synaptic_models/synapses_STP'
synapses.load()
syn = synapses.syn_params_dicts(syn_dir=syn_dir)

# Build your edges into the networks
build_edges(networks, edge_definitions, edge_params, edge_add_properties, syn)


##############################################################################
############################  EDGE EFFECTS EDGES  ############################

if edge_effects:
    # These rules are for edge effect edges. They should mimic the connections
    # created previously but using unidirectional connector.
    # Re-use the connector params set above.

    # Find core network edge types that need shell connections
    core_network_name = 'cortex'
    core_edge_def = []
    for edge in edge_definitions:
        network_name = edge['network']
        if network_name != core_network_name:
            continue
        is_core = True
        edge_src_trg = edge.get('edge')
        if edge_src_trg:
            for net_type in ('source_network', 'target_network'):
                net_name = edge_src_trg.get(net_type)
                if net_name is not None and net_name != core_network_name:
                    is_core = False
        if is_core:
            core_edge_def.append(edge)

    # Automatically set up network edges and parameters for shell network
    # Only connections from shell to core is needed, so UnidirectionConnector
    # is used, and parameters are extracted from connectors used in core edges.
    shell_network_name = 'shell'
    shell_edges = []
    shell_edge_params = {}
    for edge in core_edge_def:
        shell_edge = edge.copy()
        edge_src_trg = shell_edge.get('edge')
        if edge_src_trg:
            edge_src_trg['source_network'] = shell_network_name
        shell_edge['param'] = shell_network_name + shell_edge['param']
        shell_edges.append(shell_edge)

        edge_params_val = edge_params[edge['param']].copy()
        connector = edge_params_val.pop('connector_object', None)
        connector_class = edge_params_val.get('connector_class')
        if (connector_class is not None and
                connector_class is not UnidirectionConnector):
            replace = True
            var_list = ('p', 'p_arg', 'n_syn')
            if connector_class is ReciprocalConnector:
                var_map = ('p0', 'p0_arg', 'n_syn0')
            elif connector_class is get_connector:
                var_map = ('p1', 'p1_arg', 'n_syn1')
            else:
                replace = False
                print("Warning: Connector method not identified. "
                      "Use the same connector class for shell edges.")
            if replace:
                edge_params_val['connector_class'] = UnidirectionConnector
                connector_params = {
                    k: connector.vars[k0] for k, k0 in zip(var_list, var_map)
                    }
                connector_params['verbose'] = connector.verbose
                edge_params_val['connector_params'] = connector_params
        shell_edge_params[shell_edge['param']] = edge_params_val

    # Check parameters
    print("\nShell edges:")
    for shell_edge in shell_edges:
        print(shell_edge)
    print("\nShell edge parameters:")
    for param, edge_params_val in shell_edge_params.items():
        print(param + ':')
        print(edge_params_val)
    print("")

    # Build your shell edges into the networks
    build_edges(networks, shell_edges, shell_edge_params,
                edge_add_properties, syn)

########################## END EDGE EFFECTS ##############################
##########################################################################


##########################################################################
############################ GAP JUNCTIONS ###############################

net = networks['cortex']

# FSI
g_gap = 0.0000726# microsiemens
# gap junction probability uncorrelated with chemical synapse

FSI_uncorr_p = 0.0450 #gotten from the fact that in V1_build, 4.5% of all possible connections were made.
gap_junc_FSI = GapJunction(
    p=FSI_uncorr_p
)
population = net.nodes(pop_name='FSI')
gap_junc_FSI.setup_nodes(source=population, target=population)

conn = net.add_edges(
    is_gap_junction=True, syn_weight=g_gap, target_sections=None,
    afferent_section_id=0, afferent_section_pos=0.5,
    **gap_junc_FSI.edge_params()
)

# LTS
g_gap = 0.00073 # microsiemens
# gap junction probability uncorrelated with chemical synapse

LTS_uncorr_p = 0.0145 # gotten from the fact that in V1_build, 1.45% of all possible connections were made.
gap_junc_LTS = GapJunction(
    p=LTS_uncorr_p
)
population = net.nodes(pop_name='LTS')
gap_junc_LTS.setup_nodes(source=population, target=population)

conn = net.add_edges(
    is_gap_junction=True, syn_weight=g_gap, target_sections=None,
    afferent_section_id=0, afferent_section_pos=0.5,
    **gap_junc_LTS.edge_params()
)


##########################################################################
###############################  BUILD  ##################################

# Save the network into the appropriate network dir
save_networks(networks, network_dir)

# Usually not necessary if you've already built your simulation config
if False:
    build_env_bionet(
        base_dir='./',
        network_dir=network_dir,
        tstop=t_sim,
        dt=dt,
        report_vars=[],
        celsius=36.0,
        spikes_inputs=[
            ('baseline', './input/baseline.h5'),
            ('shell', './input/shell.h5')
        ],  # (Population for which spikes will be generated, file name)
        components_dir='components',
        config_file='config.json',
        compile_mechanisms=False
    )
