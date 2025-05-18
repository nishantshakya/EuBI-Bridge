

def get_printables(
                   axes: str,
                   shapedict: dict,
                   scaledict: dict,
                   unitdict: dict
                   ):
    dimensions = [dim for dim in axes if dim in scaledict.keys()]

    rows = [("Dimension", "Size (pixels)", "Scale", "Unit")]
    for i, dim in enumerate(dimensions):
        # size = shape[i] if i < len(shape) else ''
        size = shapedict.get(dim, '')
        scale = scaledict.get(dim, '')
        unit = unitdict.get(dim, '')
        rows.append((dim, str(size), str(scale), unit))

    col_widths = [max(len(str(row[i])) for row in rows) for i in range(4)]

    printables = []

    # ANSI escape codes for bold
    BOLD = '\033[1m'
    RESET = '\033[0m'

    for idx, row in enumerate(rows):
        line = "  ".join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(row))
        if idx == 0:
            line = BOLD + line + RESET
        printables.append(line)

    return printables

def print_printable(printable):
    for item in printable:
        print(item)

# def show_pixel_meta(input_path):
#     # input_path = f"/home/oezdemir/PycharmProjects/dask_env1/data/tifflist"
#     base = BridgeBase(input_path)
#     base.read_dataset(True)
#     base.digest()
#     base.compute_pixel_metadata()
#     ###
#     printables = {}
#     for path, vmeta in base.pixel_metadata.vmetaset.items():
#         shape = vmeta.shape
#         scaledict = vmeta.scaledict
#         unitdict = vmeta.unitdict
#         printable = get_printables(shape,scaledict,unitdict)
#         printables[path] = printable
#     for path, printable in printables.items():
#         print('---------')
#         print(f"")
#         print(f"Metadata for '{path}':")
#         print_printable(printable)
