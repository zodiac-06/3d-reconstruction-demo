from scale import calculate_scale
from mesh_scaler import scale_obj_mesh


def run_pipeline(known_length, measured_length):
    """
    Run the Track C calibration and scaling pipeline.
    """

    # Step 1: Calculate scale factor
    scale_factor = calculate_scale(
        known_length,
        measured_length
    )

    # Step 2: Scale the OBJ mesh
    input_mesh = "mesh.obj"
    output_mesh = "scaled_mesh.obj"

    scale_obj_mesh(
        input_mesh,
        output_mesh,
        scale_factor
    )

    # Step 3: Return the result
    return {
        "known_length": known_length,
        "measured_length": measured_length,
        "scale_factor": scale_factor,
        "input_mesh": input_mesh,
        "output_mesh": output_mesh,
        "unit": "meters"
    }