import fire
from eubi_bridge.ebridge import EuBIBridge
import multiprocessing as mp
import sys


def main():
    """
    Main function for the CLI interface.

    If the platform is Windows, sets the multiprocessing start method to "spawn".
    This is necessary because the default start method "fork" is not supported on Windows.
    """
    if sys.platform == "win32":
        mp.set_start_method("spawn", force=True)
    # Fire is a library for automatically generating CLIs from Python objects.
    # It uses the object's methods and docstrings to generate the CLI.
    fire.Fire(EuBIBridge)


if __name__ == "__main__":
    main()
