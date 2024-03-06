


# Utility Functions
def find_convergence(uni_connections, dist_num_cell_B, rec_connections=0):
    """
    Calculates the probability of a given edge being created in a homogenous context from Cell Type A -> Cell Type B. Convergence is calculated by dividing the 
    total connections by the total amount of Cell Type B (both gotten from a distant dependent model). 
    uni_connections: number of unidirectional connections from Cell Type A -> Cell Type B
    dist_num_cell_B: number of Cell Type B in the distant dependent model
    rec_connections: number of reciprocal connections from the two cells(only applies if cells are of same type)
    """
    total_connections = uni_connections + rec_connections
    convergence = total_connections / dist_num_cell_B
    return convergence


def homo_edge_probability(convergence, homo_num_cell_A, connect_to_same_type=False):
    """
    Calculates the probability of a given edge being created in a homogenous context from Cell Type A -> Cell Type B. The convergence is divided by the total number 
    of Cell Type A in the homogenous model to get the probability. If the connection is to a cell of the same type, then it accounts for not wanting a synapse 
    to itself.
    convergence: convergence of connections from Cell Type A -> Cell Type B
    homo_num_cell_A: number of Cell Type A in the homogenous model
    connect_to_same_type: boolean for whether the two cells are of the same type
    """
    if connect_to_same_type:
        probability = convergence / (homo_num_cell_A - 1)
    else:
        probability = convergence / homo_num_cell_A
    return probability


def homo_edge_probability_from_D(uni_connections, dist_num_cell_B, homo_num_cell_A, rec_connections=0, connect_to_same_type=False):
    """
    Calculates the probability of a given edge being created in a homogenous context from Cell Type A -> Cell Type B. If the connection is to a cell of the 
    same type, then it accounts for not wanting a synapse to itself.
    uni_connections: number of unidirectional connections from Cell Type A -> Cell Type B
    dist_num_cell_B: number of Cell Type B in the distant dependent model
    homo_num_cell_A: number of Cell Type A in the homogenous model
    rec_connections: number of reciprocal connections from the two cells(only applies if cells are of same type)
    connect_to_same_type: boolean for whether the two cells are of the same type
    """
    convergence = find_convergence(uni_connections,dist_num_cell_B, rec_connections)
    return homo_edge_probability(convergence, homo_num_cell_A, connect_to_same_type) 