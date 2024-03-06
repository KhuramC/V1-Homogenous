


# Utility Functions
def find_convergence(uni_connections, num_cell_B, rec_connections=0):
    """
    Calculates the convergence of a given edge being created from Cell Type A -> Cell Type B. Convergence is calculated by dividing the 
    total connections by the total amount of Cell Type B (both gotten from biology or another model). 
    
    Parameters:
        uni_connections: number of unidirectional connections from Cell Type A -> Cell Type B
        num_cell_B: number of Cell Type B in the original context
        rec_connections: number of reciprocal connections from the two cells(only applies if cells are of same type) 
    """
    total_connections = uni_connections + rec_connections
    convergence = total_connections / num_cell_B
    return convergence


def homo_edge_probability_from_convergence(convergence, homo_num_cell_A, connect_to_same_type=False):
    """
    Calculates the probability of a given edge being created in a homogenous context from Cell Type A -> Cell Type B based on a given convergence value. 
    The convergence is divided by the total number of Cell Type A in the homogenous model to get the probability. If the connection is to a cell of the same type, 
    then it accounts for not wanting a synapse to itself. If they are not in the same network (i.e. a virtual Cell Type A connecting to a biophysical Cell Type A), 
    then this exception does not apply.
    
    Parameters:
        convergence: convergence of connections from Cell Type A -> Cell Type B
        homo_num_cell_A: number of Cell Type A in the homogenous model
        connect_to_same_type: boolean for whether the two cells are of the same type
    """
    if connect_to_same_type:
        probability = convergence / (homo_num_cell_A - 1) #don't want synapse to itself
    else:
        probability = convergence / homo_num_cell_A
    return probability


def homo_edge_probability(uni_connections, num_cell_B, homo_num_cell_A, rec_connections=0, connect_to_same_type=False):
    """
    Calculates the probability of a given edge being created in a homogenous context from Cell Type A -> Cell Type B based on biology or another model.
    If the connection is to a cell of the same type, then it accounts for not wanting a synapse to itself. If they are not in the same network 
    (i.e. a virtual Cell Type A connecting to a biophysical Cell Type A), then this exception does not apply.
    
    Parameters:
        uni_connections: number of unidirectional connections (either wanted or from other data) from Cell Type A -> Cell Type B 
        num_cell_B: number of Cell Type B (either in the homogenous model or from other data)
        homo_num_cell_A: number of Cell Type A in the homogenous model
        rec_connections: number of reciprocal connections from the two cells(only applies if cells are of same type)
        connect_to_same_type: boolean for whether the two cells are of the same type
    """
    convergence = find_convergence(uni_connections, num_cell_B, rec_connections)
    return homo_edge_probability_from_convergence(convergence,homo_num_cell_A,connect_to_same_type) 