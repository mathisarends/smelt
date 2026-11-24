from app.factory import Sdk


class Plain:
    sdk: Sdk

    def use(self):
        self.sdk.api.call()
