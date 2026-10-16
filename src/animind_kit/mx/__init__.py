"""kit 的墨线（moxian）实现：文字、组件、布局。Manim 版在 ../text.py、../widgets/、../layout.py。"""

# 先把组件登记表导入完整：widgets.REGISTRY 里引用了 area.AreaModel，而 area 又继承 widgets.Widget；
# 不管谁先 import mx.area 还是 mx.widgets，都从这里按 widgets → area 的顺序初始化，避免循环导入出错。
from . import widgets  # noqa: E402,F401
