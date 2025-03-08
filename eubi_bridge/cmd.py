import fire
from eubi_bridge.ebridge import EuBIBridge

def eubibridge_cmd():
    _ = fire.Fire(EuBIBridge)
    return