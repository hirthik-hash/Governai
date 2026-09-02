class GovernanceFSM:
    def __init__(self):
        self.state = None

    def start(self):
        self.state = "initialized"
