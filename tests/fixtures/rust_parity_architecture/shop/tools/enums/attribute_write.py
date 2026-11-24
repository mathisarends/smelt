from .kinds import Color

Color.extra = 1
Color.RED.value = "store"
del Color.GREEN.value
value = Color.RED.value
