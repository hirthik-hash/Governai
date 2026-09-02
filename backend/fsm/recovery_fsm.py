class RecoveryFSM:
    def __init__(self):
        self.recovery_mode = False

    def trigger(self):
        self.recovery_mode = True
