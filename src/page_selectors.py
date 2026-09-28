"""
页面选择器集中管理 —— 页面改版时只改这一个文件。

文件名说明：不要叫 selectors.py，会与 Python 标准库的 selectors 模块冲突。

数据来源：docs/页面结构.md，勘察导出 recon_output/20260927_113747
勘察日期：2026-09-27

重要：本系统是 Ext JS 2.x 老框架，工具栏按钮的 id（ext-gen18 等）由框架动态生成，
每次加载都可能变化，因此一律使用「文本定位」，禁止写死 id。
"""

# ---------------------------------------------------------------- 入口地址

OA_HALL_URL = "https://oa.lawyers.org.cn/hall/"
TALENT_EXCHANGE_URL = "https://www.lawyers.org.cn/member/talentexchange"
# 招聘管理后台（真正的操作页）
RECRUIT_MANAGER_URL = "https://recruitment.lawyers.org.cn/manager/index.jsp"

# ---------------------------------------------------------------- 列表网格

GRID_ROW = "div.x-grid3-row"                     # 每一行
GRID_ROW_SELECTED = "div.x-grid3-row-selected"   # 被选中的行（ExtJS 自动加的 class）
GRID_CELL = "td.x-grid3-td"                      # 行内单元格

# 列顺序（0 起）：招聘标题/CompanyID/招聘单位/发布日期/结束日期/最新更新/排序时间/招聘岗位/招聘人数/状态
COL_TITLE = "td.x-grid3-td-0"
COL_COMPANY_ID = "td.x-grid3-td-1"
COL_COMPANY = "td.x-grid3-td-2"
COL_PUBLISH_DATE = "td.x-grid3-td-3"
COL_END_DATE = "td.x-grid3-td-4"
COL_LAST_UPDATE = "td.x-grid3-td-5"
COL_SORT_TIME = "td.x-grid3-td-6"                # ★ 上次置顶时间，20 天计时的依据
COL_POSITIONS = "td.x-grid3-td-7"
COL_HEADCOUNT = "td.x-grid3-td-8"
COL_STATUS = "td.x-grid3-td-9"


def col(index: int) -> str:
    """按列序号返回单元格选择器"""
    return f"td.x-grid3-td-{index}"


# ---------------------------------------------------------------- 工具栏按钮

BTN_CREATE = 'button:has-text("创建新招聘")'
BTN_EDIT = 'button:has-text("修改")'
BTN_DELETE = 'button:has-text("删除")'
BTN_PUBLISH = 'button:has-text("发布招聘")'
BTN_FINISH = 'button:has-text("结束招聘")'
BTN_UPDATE_SORT_TIME = 'button:has-text("更新排序时间")'   # ★ 核心动作
BTN_SYNC_COMPANY = 'button:has-text("同步招聘单位信息")'
BTN_REFRESH = 'button:has-text("刷新")'

# ---------------------------------------------------------------- 登录兜底

# 登录态复用失败时才走账号密码登录。OA 登录页输入框的真实选择器尚未逐一确认，
# 这里保留候选列表逐个尝试，确认后收敛为单个值。
LOGIN_USERNAME_CANDIDATES = [
    'input[name="username"]', 'input[name="user"]', 'input[name="loginName"]',
    'input[name="account"]', 'input[name="j_username"]', 'input#username',
    'input#user', 'input[type="text"]',
]
LOGIN_PASSWORD_CANDIDATES = [
    'input[name="password"]', 'input[name="pass"]', 'input[name="j_password"]',
    'input#password', 'input[type="password"]',
]
LOGIN_SUBMIT_CANDIDATES = [
    'button[type="submit"]', 'input[type="submit"]',
    'button:has-text("登录")', 'a:has-text("登录")',
    'input[value="登录"]', 'text=登录',
]
# URL 中出现这些关键字即判定为「未登录」
LOGIN_URL_MARKERS = ("login.jsp", "passport", "loginSuccessUrl")

# ---------------------------------------------------------------- 业务常量

REPOST_INTERVAL_DAYS = 20            # 满 20 天才能再次置顶
STATUS_RECRUITING = "招聘中"           # 列表「状态」列的有效值
SORT_TIME_FORMAT = "%Y-%m-%d %H:%M"  # 排序时间格式，如 2026-09-24 09:34

# 点按钮后弹出的原生 confirm 文本特征，用于确认点对了按钮、没有误触别的操作
CONFIRM_TEXT_KEYWORD = "更新排序时间"

# 官方规则原文（来自确认弹窗）：
#   「更新排序时间的操作在20天之内只有使用一次置顶的机会，请确认是否使用？」
# 解读：每 20 天周期内仅有 1 次置顶机会，用过即消耗。
# 因此：必须确认满 20 天后才点击，且每个周期只点一次，绝不能重复点击。
