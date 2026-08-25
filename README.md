# RR 数据定时推送 📧

定时从 RR（RoundRobin 客服分配系统）拉取 **近 24 小时工单分配流水** 与 **客服上下线记录**，
本地去重累积（分配接口只有 24 小时滚动窗口，不轮询会丢数据），按你现有《RR API数据.xlsx》
的样式生成 Excel 报表，定时邮件推送到指定收件人。

支持 **一行命令部署**、**密钥/邮箱/时间全可配置**、**无需重启即时生效**。

---

## ✨ 功能特性

- 🔁 **定时拉取 + 本地累积** —— 分配流水按唯一 id 去重、上下线会话按「客服+上线时间」去重，
  累积进本地 SQLite，突破接口 24 小时窗口限制
- 📊 **报表样式与现有表格一致** —— Arial 10、时间列 `yyyy-mm-dd hh:mm:ss`、冻结首行、
  自动筛选、🟢 在线中 / 已下线状态
- 👥 **按客服分组展示** —— 分配明细按「班次 → 客服」分组排列，同一客服集中显示；
  「按客服汇总」页统计每天每人的分配工单数（去重）与记录数
- 🌙 **班次自动判定** —— 夜班名单固定（可配置），其他人按分配时间自动推断白班/中班，
  兼容负责人提前 10-20 分钟接单的情况
- ⏱ **在线时长** —— 上下线页新增「在线时长(分钟)」，进行中的会话按报表时刻实时计算
- 📎 **Excel 附件邮件推送** —— 支持每天多个时间点推送，正文附带数据统计
- 👥 **多收件人管理** —— 随时新增 / 删除收件邮箱
- 🔧 **全配置可改** —— RR 密钥、发件邮箱、拉取间隔、推送时间都能在菜单里改
- 🔒 **密钥安全** —— 敏感信息只在本地 `config.json`（600 权限），绝不进入仓库
- 🧩 **依赖极简** —— 仅需 openpyxl，HTTP 拉取用 Python 标准库，虚拟环境隔离
- 🚀 **一行部署** —— `curl | bash` 搞定

---

## 🚀 部署

### 方式一：项目目录已在服务器上

```bash
cd rr-report
./install.sh
```

### 方式二：一行命令

```bash
curl -fsSL https://raw.githubusercontent.com/BlizzardSaber/rr-report/main/install.sh | bash
```

（也可以先 `git clone https://github.com/BlizzardSaber/rr-report.git` 再进入目录 `./install.sh`。）

部署脚本会引导完成：RR 接口地址与密钥 → 发件 SMTP → 收件人 → 拉取间隔 / 推送时间 → 注册 cron，
全程交互式填表，回车保留默认值。也可以先手动把目录 scp 到服务器再就地安装。

> 服务器要求：Linux 或 macOS，Python 3.8+（缺失时脚本会尝试自动装），能访问 RR 接口和 SMTP 服务器。

---

## 🛠 日常管理

进入交互菜单，所有操作即改即生效（下次 cron 自动用新配置）：

```bash
cd rr-report && ./manage.sh
```

```
====== RR 数据推送管理菜单 ======
  1) 查看当前配置
  2) 新增收件人邮箱
  3) 删除收件人邮箱
  4) 修改发件邮箱（SMTP）
  5) 修改 RR 接口配置（地址/密钥）
  6) 修改数据拉取间隔
  7) 修改报表推送时间
  8) 立即拉取一次数据
  9) 立即生成并发送一次报表（测试）
 10) 查看数据统计与最近日志
 11) 查看 cron 任务
 12) 修改夜班人员名单
 13) 卸载
  0) 退出
```

手动跑一次（不进菜单）：

```bash
.venv/bin/python src/run_pull.py        # 只拉取入库
.venv/bin/python src/run_push.py --test # 发测试邮件（带【测试】标记）
.venv/bin/python src/run_push.py --dry-run  # 只生成表格不发邮件
```

---

## ⏱ 定时机制说明

| 任务 | 配置项 | 默认 | cron 形式 |
|------|--------|------|-----------|
| 数据拉取 | `schedule.pull_interval_minutes` | 30 分钟 | `*/30 * * * *` |
| 报表推送 | `schedule.push_times`（可多个） | 每天 09:00 | `0 9 * * *`（每个时间点一行） |

- 推送时间以**服务器本地时区**为准，install.sh 会尝试把 Linux 服务器时区设为 Asia/Shanghai；
  报表内的时间统一按 UTC+8 显示（`rr.tz_offset_hours` 可改）。
- **拉取间隔建议 ≤ 60 分钟**：分配接口是 24 小时滚动窗口，间隔太长中间的数据会永久丢失。
- 推送前会先拉一次最新数据，即使拉取 cron 偶发失败，报表也会用本地累积数据兜底生成。

---

## 📧 常见 SMTP 配置速查表

| 服务商 | SMTP 主机 | 端口 | 加密 | 密码字段 |
|--------|-----------|------|------|----------|
| QQ 邮箱 | `smtp.qq.com` | 465 | SSL | **授权码**（设置→账户→开启 SMTP） |
| 163 邮箱 | `smtp.163.com` | 465 | SSL | **授权码**（设置→POP3/SMTP） |
| Gmail | `smtp.gmail.com` | 465 / 587 | SSL / STARTTLS | **应用专用密码** |
| 企业微信邮箱 | `smtp.exmail.qq.com` | 465 | SSL | 邮箱密码 |
| Outlook / Office365 | `smtp.office365.com` | 587 | STARTTLS | 账户密码 |

> ⚠️ 多数国内邮箱不能直接用登录密码发信，需要单独开启 SMTP 并生成「授权码」，填到密码字段。

---

## 📂 项目结构

```
rr-report/
├── install.sh               # 一行部署脚本
├── uninstall.sh             # 卸载脚本
├── manage.sh                # 交互式管理菜单
├── config/
│   ├── config.example.json  # 配置模板（提交）
│   └── config.json          # 真实配置（gitignore，含密钥）
├── src/
│   ├── config_utils.py      # 配置读写 + 校验 + cron 行生成
│   ├── fetch_rr.py          # 拉取两个 RR 接口（标准库 urllib）
│   ├── store.py             # SQLite 累积存储（去重合并）
│   ├── pipeline.py          # 拉取→入库流水线（拉取/推送共用）
│   ├── build_xlsx.py        # 按《RR API数据.xlsx》样式生成报表
│   ├── send_email.py        # SMTP 发邮件
│   ├── run_pull.py          # cron 入口：定时拉取
│   ├── run_push.py          # cron 入口：生成报表并推送
│   ├── show_status.py       # 数据统计查看（菜单用）
│   └── common.py            # 日志 + 单实例锁
├── data/                    # rr_data.db 累积数据库（gitignore）
├── logs/                    # 运行日志（gitignore）
└── output/                  # 生成的报表 xlsx（gitignore）
```

---

## ⚙️ 配置说明（config.json）

```json
{
  "rr":  { "base_url": "https://your-pod.roundrobin-assignment.com/call/12345",
           "api_key": "URL 里 p1= 后面的 UUID",
           "assignment_proc": "___get_assignment_log",
           "availability_proc": "___get_agent_availability",
           "tz_offset_hours": 8 },
  "smtp": { "host": "...", "port": 465, "use_ssl": true, "username": "...", "password": "...", "from_name": "RR数据机器人" },
  "recipients": ["a@example.com", "b@example.com"],
  "schedule": { "pull_interval_minutes": 30, "push_times": ["09:00"] },
  "report": {
    "assignment_window_hours": 0,
    "availability_days": 14,
    "night_shift_agents": [],
    "shifts": { "day": ["08:30", "18:00"], "mid": [["14:00", "18:00"], ["20:00", "23:00"]] }
  }
}
```

通常不需要手改，用 `./manage.sh` 即可。`report` 各项说明：
- `assignment_window_hours`：分配明细取数窗口，`-1` = 本自然月（默认，每月一张新表，
  上月数据不再展示但仍存档于数据库）、`0` = 全部累积、`>0` = 近 N 小时；
  `availability_days`：上下线明细取数窗口，`-1` = 本自然月（默认）、`0` = 全部累积、
  `>0` = 近 N 天
- `night_shift_agents`：永远是夜班的客服名单
- `shifts`：白班/中班时段（UTC+8，中班支持多段），用于自动推断非名单客服的班次

**班次判定规则**（按「人 + 日期」逐天判定）：
1. 夜班名单内的人永远夜班（夜班固定，不依赖班表）；
2. **班表兜底优先**：项目目录放《客户专家班表.xlsx》（`report.schedule_file`
   可改路径），单元格「中」→中班、「班」→白班；按日期粒度生效——班表里
   没有该日期（如月份未更新）自动回退行为逻辑；
3. 行为逻辑：每天只看当天的登录与分配——上午（12:00 前）有活动 → 白班
   （证据优先）；无上午但晚间（18:00 后）有活动 → 中班；仅下午活动时，
   踩中班上班点（14:00 前后 10 分钟内）开始的判中班，更晚出现的判白班
   （临时支援）。报表每次从全量数据重新生成，晚间数据到来后自动修正。
   非名单的人永远不会被判成夜班。

---

## ❓ FAQ

**Q: 报表里「分配数据」为什么有时比接口单次返回的还多？**
A: 本地库累积了持续轮询的数据，按报表时刻往前推 24 小时取数，接口偶发漏数或请求失败也不影响完整性。

**Q: 邮件进了垃圾箱？**
A: 让收件人把发件地址加入通讯录/白名单；或检查发件域名是否配置了 SPF/DKIM。

**Q: cron 到点了没触发？**
A: macOS 需在「系统设置 → 隐私与安全性 → 完全磁盘访问权限」给 `/usr/sbin/cron` 授权；
Linux 用 `grep CRON /var/log/syslog` 排查。查看日志：`tail -f logs/cron.log`。

**Q: RR 的 key 换了怎么办？**
A: `./manage.sh` 选 5，回车跳过不变的部分，只改密钥即可，无需重启任何东西。

**Q: 怎么更新到新版本？**
A: 重跑部署命令即可（会保留 `config.json`、`data/` 里的累积数据和日志）。

---

## 🗑 卸载

```bash
cd rr-report && ./uninstall.sh
```

会移除全部 cron 任务（拉取 + 推送），并询问是否删除整个目录（含累积数据、配置和日志）。

---

## 📜 License

仅供内部使用。
