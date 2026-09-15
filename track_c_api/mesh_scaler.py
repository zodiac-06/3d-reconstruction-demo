from pathlib import Path


def scale_obj_mesh(input_file, output_file, scale_factor):
    """
    Scale an OBJ mesh using the given scale factor.
    """

    input_file = Path(input_file)
    output_file = Path(output_file)

    if not input_file.exists():
        raise FileNotFoundError(
            f"Mesh file not found: {input_file}"
        )

    with open(input_file, "r") as file:
        lines = file.readlines()

    scaled_lines = []

    for line in lines:

        if line.startswith("v "):
            parts = line.strip().split()

            x = float(parts[1]) * scale_factor
            y = float(parts[2]) * scale_factor
            z = float(parts[3]) * scale_factor

            scaled_lines.append(
                f"v {x} {y} {z}\n"
            )

        else:
            scaled_lines.append(line)

    with open(output_file, "w") as file:
        file.writelines(scaled_lines)

    return str(output_file)