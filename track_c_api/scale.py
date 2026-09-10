def calculate_scale(known_length, measured_length):
    """
    Calculate scale factor.

    known_length:
        Real-world/reference length

    measured_length:
        Length measured from the unscaled model
    """

    if measured_length <= 0:
        raise ValueError("Measured length must be greater than zero.")

    return known_length / measured_length