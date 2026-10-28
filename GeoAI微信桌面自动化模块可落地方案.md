# GeoAI 微信桌面自动化模块：可落地方案

## 目标与原则

针对 Windows 微信 4.x、Electron、Python 与 GeoAI CRM，建议先实现一个独立的 Python 本地服务，负责微信状态检测、联系人匹配和任务管理。首期数据读取优先使用官方允许的导出方式或用户可访问的界面信息；不破解微信数据库，也不读取微信进程内存中的密钥。

微信 4.x 的界面自动化兼容性需要实机验证。部分版本采用自绘界面，传统 UI Automation（UIA）可能无法获取完整控件树。第三方库 `wxauto4` 声称支持部分 4.1 版本，但它不是微信官方组件，具体可用性需在目标电脑和客户端版本上验证。

核心原则：微信通讯录中存在的信息，不等于程序可以直接导出的信息。对无法读取的手机号或微信号，应由用户通过合法来源补充，不应尝试破解获取。

## 一、界面读取与本地数据读取

| 比较项 | 界面读取（UIA/OCR） | 直接读取本地微信数据库 |
| --- | --- | --- |
| 主要方式 | 识别当前微信窗口及可访问内容 | 解析本地数据库文件 |
| 昵称、备注 | 取决于界面和控件开放程度 | 取决于数据结构及字段 |
| 手机号、微信号 | 不保证全部可见或可获取 | 不保证数据库包含完整字段 |
| 稳定性 | 受界面更新、窗口状态影响 | 受加密、版本及数据结构影响 |
| 安全与隐私 | 优先选择，只读取必要信息 | 数据访问风险较高 |
| 建议 | 第一阶段采用 | 不作为首期方案 |

部分开源项目声称可扫描微信进程内存获取数据库密钥并解密本地数据库。这不是官方支持的接口，会增加安全、隐私和维护风险，不建议作为 CRM 基础能力。

## 二、桌面端整体架构

```text
GeoAI Electron 桌面端
├── 微信状态、通讯录、CRM 匹配、任务列表
└── Python 本地服务
    ├── FastAPI 接口
    ├── 任务队列
    ├── UI 自动化适配器
    ├── 微信状态检测：进程、窗口、登录状态
    ├── 联系人匹配：昵称、备注、授权字段
    ├── 好友申请工作台：单条核验、人工确认
    └── 任务记录：SQLite、审计日志
```

建议将微信功能独立为 `wechat_service`，避免与 CRM 页面和视频发布模块耦合。微信客户端升级时，只需重点维护微信适配层。

建议项目目录：

```text
geoai-desktop/
├── electron/
│   └── src/
│       └── pages/
│           └── WeChatManager.vue
├── python/
│   └── wechat_service/
│       ├── main.py             # FastAPI 入口
│       ├── wechat_detector.py  # 微信进程与登录状态
│       ├── contact_reader.py   # 可访问联系人信息
│       ├── contact_matcher.py  # CRM 联系人匹配
│       ├── task_manager.py     # 任务状态流转
│       ├── ui_adapter.py       # 微信界面适配
│       └── database.py         # SQLite 持久化
└── shared/
    └── wechat_types.py
```

## 三、联系人数据结构

CRM 只需增加少量字段，无需另建一套复杂客户管理系统。

| 字段 | 示例 | 用途 |
| --- | --- | --- |
| `crm_contact_id` | `C10001` | 关联现有 CRM 客户 |
| `phone` | `138****1234` | 手机号匹配 |
| `wechat_id` | 用户提供的微信号 | 微信号匹配 |
| `wechat_nickname` | 张先生 | 展示与核验 |
| `wechat_remark` | 上海客户 | 辅助区分 |
| `match_status` | `matched` | 匹配结果 |
| `friend_status` | `pending_review` | 好友任务状态 |
| `last_error` | 未找到结果 | 记录失败原因 |

手机号应先统一格式再比较；微信号应按微信实际规则处理，不能把昵称当成唯一标识。手机号命中多个候选人时，应交由人工核验，不能自动选择第一个。

## 四、Python 模块示例

### 检测微信进程

检测到进程只表示微信正在运行，不能据此认定用户已登录。后续还需结合窗口可见状态、登录页特征等判断；无法确认时应返回 `unknown`，提示用户手动检查。

```python
import psutil


def detect_wechat_process():
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            name = (proc.info["name"] or "").lower()
            if name in {"weixin.exe", "wechat.exe"}:
                return {"running": True, "pid": proc.info["pid"]}
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return {"running": False, "pid": None}
```

### 匹配 CRM 联系人

此函数只负责匹配，不负责从微信内部提取数据。`available_contacts` 应来自用户授权且实际可访问的数据源。

```python
def normalize_phone(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit())


def match_contact(crm_contact, available_contacts):
    phone = normalize_phone(crm_contact.get("phone", ""))
    wechat_id = (crm_contact.get("wechat_id") or "").strip()
    candidates = []

    for contact in available_contacts:
        contact_phone = normalize_phone(contact.get("phone", ""))
        contact_wechat_id = (contact.get("wechat_id") or "").strip()

        phone_match = bool(phone) and contact_phone == phone
        wechat_match = (
            bool(wechat_id)
            and contact_wechat_id.casefold() == wechat_id.casefold()
        )

        if phone_match or wechat_match:
            candidates.append(contact)

    if len(candidates) == 1:
        return {"status": "matched", "contact": candidates[0]}
    if len(candidates) > 1:
        return {"status": "multiple_matches", "candidates": candidates}
    return {"status": "unmatched", "candidates": []}
```

## 五、好友申请工作流

将好友申请设计为人工确认的单条工作流，不通过自动逐个发送来规避风控。

1. **准备联系人**：从 CRM 选择一位联系人，展示搜索依据和已有匹配信息。
2. **搜索与核验**：用户确认后，在微信“添加朋友”界面查询手机号或微信号，并核对搜索结果。
3. **用户确认申请**：展示目标资料和申请说明，由用户决定是否发送。程序不自动连续发送陌生人申请，也不尝试规避平台限制。
4. **记录结果**：用户确认发送后记录为“已提交待确认”；对方接受后再更新为“已成为好友”。不能仅凭点击按钮认定添加成功。

建议任务状态：

```text
pending → reviewing → pending_confirmation → submitted → accepted
```

其他状态：`not_found`、`ambiguous`、`failed`、`cancelled`。如果微信出现安全验证、操作限制或异常提示，应暂停后续操作，交由用户处理。

## 六、桌面端页面

页面可分为三个区域：

- **顶部**：微信连接状态、当前登录账号、刷新按钮。
- **中间**：CRM 联系人表格，显示手机号、微信号和匹配状态。
- **右侧**：联系人详情、核验结果和申请记录。

第一版不加入多账号群控、复杂规则引擎或自动重试发送。

## 七、开发顺序

1. **第一阶段**：检测微信运行与登录状态，确认当前微信 4.x 版本能读取哪些界面字段。
2. **第二阶段**：建立联系人数据模型，实现 CRM 匹配、重复检查和 SQLite 记录。
3. **第三阶段**：实现单条搜索、结果展示与人工确认工作流。
4. **第四阶段**：集成 Electron 页面，补充错误提示、日志和版本兼容验证。

建议先完成第一阶段的可运行验证程序。微信 4.x 不同小版本的界面开放程度不同，先确认真实可读取的字段，再扩展完整自动化流程。
