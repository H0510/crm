# 微信获客模块使用说明

这个目录里是一套本地 Python 服务，给 Electron 桌面端提供微信状态检测、界面文字读取、CRM 联系人匹配和好友申请任务管理。界面读取只做截图和 OCR，不注入微信进程，也不读取微信数据库。

## 目录结构

```
python/
├── requirements.txt
├── data/                        # 运行时生成的 SQLite 数据库
├── wechat_service/
│   ├── wechat_detector.py       # 进程、窗口、登录状态
│   ├── screen_reader.py         # 窗口截图 + 系统 OCR
│   ├── ui_adapter.py            # 界面读取适配层 + UIA 能力探测
│   ├── contact_reader.py        # 界面文字转联系人候选
│   ├── contact_matcher.py       # CRM 联系人匹配
│   ├── database.py              # SQLite 持久化
│   ├── task_manager.py          # 好友申请任务状态机
│   ├── friend_search.py         # 添加朋友搜索与结果核验
│   ├── friend_sender.py         # 单条好友申请的点击发送
│   └── main.py                  # FastAPI 入口
└── tests/                       # 单元测试与真实环境集成测试
```

`shared/wechat_types.py` 放状态常量与数据结构，Python 侧统一从这里取；`electron/src/pages/WeChatManager.vue` 是对应的管理页面，需要放进已有的 Electron + Vue3 工程。

## 环境要求

Windows 10/11，Python 3.11 以上。实测环境为 Python 3.13.15、微信 4.1.13.12。

```powershell
cd e:\我的项目\crm\python
python -m pip install -r requirements.txt
```

系统 OCR 依赖 Windows 自带的识别引擎，中文需要繁体/简体语言包。检查方式：

```powershell
powershell -NoProfile -Command "[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]; [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag }"
```

本机输出只有 `zh-Hans-CN`，够用；如果为空，需要先在系统设置里安装中文语言包。

## 启动服务

```powershell
cd e:\我的项目\crm\python
python -m uvicorn wechat_service.main:app --host 127.0.0.1 --port 8756
```

启动后访问 `http://127.0.0.1:8756/docs` 可以看到完整的接口文档。数据库文件默认落在 `python/data/wechat_service.db`，首次启动自动建表。

## 使用流程

1. 打开微信并登录，把主窗口留在桌面上，不要最小化到托盘。托盘状态截不到画面，登录状态只会返回 `unknown`。
2. 前端或 `GET /api/wechat/status` 查看状态：`running`、`login_state`、`account`、`evidence`。`evidence` 会写清楚判断依据，判不准就是 `unknown`，不会硬猜。`account` 只在界面出现「微信号：xxx」时才有值，主界面不展示时返回 `null`。
3. 点「读取微信界面」调 `POST /api/contacts/scan`，服务会逐个窗口截图做 OCR，抽出手机号（精确/掩码）、微信号（`wxid_` 开头）和昵称候选。窗口停在登录页时直接跳过，并在 `errors` 里写明原因。
4. 在 CRM 联系人里录入 `crm_contact_id`、手机号、微信号，调 `POST /api/contacts/{id}/match` 做匹配。命中一条是 `matched`，命中最多条是 `multiple_matches`，需要人工核验，不会自动选第一个。
5. 需要加好友时，对这一位联系人调 `POST /api/tasks` 建任务，状态从 `pending` 开始。
6. 由用户在微信里打开「添加朋友」窗口并留在屏幕上。页面点「复制搜索词并读取结果」调 `POST /api/tasks/{id}/search`：服务会把手机号/微信号放进剪贴板、把窗口切到前台，然后读取当前搜索结果给出核验结论。也可以点「自动输入并读取结果」，在用户二次确认后由服务发送一次 Ctrl+V。微信搜索框是边输边搜，服务不按回车，因为回车会打开「申请添加朋友」弹窗。
7. 核验结论分四种：`matched`（微信号或手机号一致）、`not_found`（界面提示用户不存在）、`ambiguous`（多个微信号或结果冲突）、`needs_review`（缺少可精确比对的字段）。结论和 OCR 原文写进任务的 `evidence.verification`，接口同时返回 `suggested_status`，但不会自动改状态。
8. 核验结论为 `matched` 后，先把任务推进到 `pending_confirmation`。页面此时出现「自动点击发送」，点击会调 `POST /api/tasks/{id}/send` 并带 `confirmed=true`。服务先 OCR 定位「添加到通讯录」并点击，再在「申请添加朋友」弹窗里按绿色按钮颜色定位「确定」并点击，只发这一条申请。
9. 申请弹窗关闭后任务自动记为 `submitted`；如果 6 秒内弹窗没有关闭，接口返回 400，任务状态不变，需要人工检查。对方通过后推进到 `accepted`；每一步都会写入 `task_events`，可以在 `GET /api/tasks/{id}/events` 里查。

服务不提供批量建任务、不循环发送、不自动重试；自动点击只允许单条、核验为 `matched`、且带 `confirmed=true` 的任务。搜索框只粘贴不按回车，因为回车会打开「申请添加朋友」弹窗。

## 通讯录采集（批量读微信号和联系方式）

1. 微信停在「通讯录 → 联系人」页，列表停在哪个位置都可以，任务开始时会先滚回顶部。
2. 页面「通讯录采集」里填采集人数和每行等待（默认 0.6 秒），点「开始采集」。程序按头像像素定位可点击的联系人行，逐个点开右侧资料卡，读微信号、电话、备注、地区，企业微信分组那种没有头像的灰色组织名会被跳过。
3. 进度条按本次识别到的联系人数走。重复采集同一个人会更新原记录，不会新增重复行；已提交过好友申请的记录也会照常更新资料，不影响添加任务。
4. 中途可以取消；滚到通讯录最底部时任务自己结束，不会停在底部反复点最后几个人。采集中途拖动微信窗口也没关系，每屏都会重新读一次窗口位置。采集结果用「导出 CSV」拿 `GET /api/candidates/export.csv`，也可以在「界面读取结果」里看最近几条。
5. 微信号只接受合法微信号格式，企业微信联系人卡片上显示的是企业名，不会被当成微信号写进库。

对应接口：`POST /api/collect/address-book`（`max_contacts` 1 到 3000，`settle_seconds` 0.3 到 2.0）、`GET /api/collect/jobs/{job_id}`、`POST /api/collect/jobs/{job_id}/cancel`、`GET /api/candidates/export.csv`。任务状态是 `running / completed / cancelled / failed`，同一时间只允许一个任务占用微信窗口。

实测：从列表顶部开始，846 人的通讯录整轮约 35 分钟。最近一轮全量跑完库里 837 条候选，682 条读到微信号，20 条读到手机号，709 条读到地区，433 条读到备注。

列表最底部几个浅色头像（白猫、纯色插画）只有中间一小块和列表底色有色差，按头像色块高度判断会漏掉整行，表现出来就是拉到最底下还有几个人没采集就直接结束了。现在按行距推算缺行、确认那个位置确实有内容再补进去，见底时最后几位（`^_^`、`。`、`□□`）都能正常点开读到微信号。另外点击后如果资料卡没有换人，会换位置多等几拍重试，仍不换人就跳过这一行，不会把上一个人的资料重复写一遍。

## 接口清单

状态类：
`GET /health`、`GET /api/wechat/status`、`GET /api/wechat/windows`、`GET /api/wechat/add-friend-window`、`POST /api/wechat/probe`、`GET /api/wechat/windows/{hwnd}/screenshot`、`POST /api/wechat/windows/{hwnd}/ocr`

联系人：
`GET /api/contacts`、`POST /api/contacts`、`GET /api/contacts/{crm_contact_id}`、`POST /api/contacts/scan`、`GET /api/candidates`、`POST /api/contacts/{crm_contact_id}/match`、`POST /api/collect/address-book`、`GET /api/collect/jobs/{job_id}`、`POST /api/collect/jobs/{job_id}/cancel`、`GET /api/candidates/export.csv`

任务：
`GET /api/tasks`、`POST /api/tasks`、`GET /api/tasks/{id}`、`POST /api/tasks/{id}/transition`、`GET /api/tasks/{id}/events`、`POST /api/tasks/{id}/search`、`POST /api/tasks/{id}/send`

`POST /api/tasks/{id}/search` 入参：`auto_input`（默认 `false`）、`confirmed`（自动输入时必须为 `true`）、`copy_to_clipboard`（默认 `true`）。它只接受 `pending / reviewing / not_found / ambiguous / failed` 这几种状态的任务，已提交或已完成的任务不会再核验。

`POST /api/tasks/{id}/send` 入参只有 `confirmed`，必须为 `true`。任务必须处于 `pending_confirmation` 且核验结论为 `matched`，否则返回 400。接口不会重试，也不会循环发送。

元信息：
`GET /api/meta/statuses`，返回全部状态值与允许的流转关系，前端按钮可以直接照着渲染。

## 本机实测结果

环境：Windows + Python 3.13.15 + 微信 4.1.13.12，微信已登录。

自动化测试 `python -m pytest tests -q`：62 项全部通过，0 失败。覆盖匹配逻辑、任务状态机、审计日志、搜索核验解析、账号识别、发送动作的按钮定位、资料页解析、HTTP 接口，以及真实微信环境集成测试。

真实微信环境实测：

- 进程与窗口：识别到 `Weixin.exe`，主窗口标题「微信」，类名 `Qt51514QWindowIcon`，位置和可见状态正常。
- 登录状态：会话列表 OCR 命中「公众号」「服务号」时判为 `logged_in`；只要存在「添加朋友」窗口，也作为已登录证据。
- 搜索核验：录入联系人 `Z25713a`，在「添加朋友」窗口搜索后，结果只有「欣怡犯困.」一张卡片。卡片不显示微信号，因此按「合法微信号精确搜索 + 唯一结果卡片」判为 `matched`，任务自动从 `pending` 推进到 `reviewing`，再由用户推进到 `pending_confirmation`。
- 自动发送：`POST /api/tasks/7/send` 带 `confirmed=true` 后，OCR 定位到「添加到通讯录」并点击，弹出「申请添加朋友」窗口。系统 OCR 认不出绿底白字的「确定」，只认出灰色「取消」，于是改从窗口底部按绿色像素定位主按钮并点击。弹窗在 1 到 2 秒后才关闭，发送逻辑改为轮询 6 秒确认，任务记为 `submitted`，联系人 `friend_status` 同步为 `submitted`，审计记录完整。
- 这次真实向「欣怡犯困」发出了好友申请，是否通过取决于对方；没有自动标记 `accepted`。
- 搜索不存在的对象时，真实窗口里出现过「无法找到该用户」，OCR 把「无法」读成「无氵去」，已用「找到该用户」「账号是否正确」等后半句做容错，并有单元测试覆盖。

## 已知限制

- 搜索结果卡片不展示微信号，`matched` 依赖「按合法微信号或手机号做精确搜索 + 界面只有一个结果卡片」。如果要更强证据，需要再打开资料页核对，当前没有做。
- 绿色「确定」按钮按颜色定位，微信换主题或改按钮颜色会失效；阈值在 `friend_sender.py` 的 `find_green_button` 里。
- 申请弹窗关闭有 1 到 2 秒延迟，已改成轮询；发送后仍建议人工扫一眼微信是否出现「等待验证」。
- 搜索框由程序自动打开「添加朋友」窗口并点进焦点，只粘贴一次、不按回车，随后点一次绿色「搜索」按钮出结果。
- OCR 对相似字符不稳定，实测把 `Z25713a` 读成 `Z2S71 3a`、`Z2S713a`；匹配时按合法微信号格式和唯一卡片兜底，展示层保留 OCR 原文。
- `POST /api/wechat/probe` 在微信登录状态下会让 `uiautomation` 抛出 `0x80010108`，进程可能不稳定；日常读取用 OCR，不要频繁调用探针。
- `uiautomation` 必须用 2.0 以上版本。1.1.15 在 Python 3.13 下退出时会触发访问冲突，把服务带崩。
- `WeChatManager.vue` 已集入 `electron/` 工程，可打包成桌面端；打包版的数据目录与工程目录相互独立。
- 通讯录采集依赖资料卡本身把字段渲染出来：实测约三成联系人读不到微信号（卡片未渲染或字符粘连），电话大多没有。读不到就留空，不会猜测补齐。
- 昵称优先取卡片顶部的显示名，顶部没读出来时退回「昵称：」字段；刻意的纯表情昵称 OCR 只能读出「0」之类的字符，属于识别上限。
