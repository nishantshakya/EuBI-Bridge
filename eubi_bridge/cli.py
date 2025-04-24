import fire
from eubi_bridge.ebridge import EuBIBridge
import multiprocessing as mp
import sys


def main():
    if sys.platform == "win32":
        mp.set_start_method("spawn", force=True)
    fire.Fire(EuBIBridge)


if __name__ == "__main__":
    main()
