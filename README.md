# 研发物料管家 RMLM

研发物料全生命周期管理系统：申请 → 直属上级审核 → ERP 出库 → 本人签收 → 使用 / 损耗 / 拆分 → 暂存 / 转交 → 退库 / 报废 → 对账关账。

- 后端：Python 3.13 + FastAPI + SQLAlchemy + **MySQL 8**（不依赖 Redis；定时任务进程内运行，多实例用 MySQL 命名锁防重）
- 前端：Vue 3 + Element Plus，一套代码同时提供电脑端管理台和手机 H5（`/m`）
- 金蝶云星空：**只读**，中台不回写金蝶。物料档案通过 WebAPI 每天增量同步（也可手动）；项目档案和领料 / 退料 / 报废单暂用手工录入或 Excel 导入，接口表单确认后实现 `ErpReader` 即可切换
- 企业微信：手机端在企微工作台打开、网页授权免密登录；通讯录每天同步人员和直属上级；站内待办同步推送企微应用消息。未配置企微时自动退回账号密码登录 + 站内待办
- 不对接仓储系统：物料码由本系统发放（RM 开头），用标签打印机打印；扫码支持 PDA 扫码枪（键盘输入）和手机摄像头

设计说明与已确认的业务规则见 [docs/系统方案.md](docs/系统方案.md)。

## 目录

```
backend/   FastAPI 应用（app/）、测试（tests/）、演示数据脚本（scripts/demo_seed.py）
frontend/  Vue 3 前端
deploy/    Dockerfile、docker-compose.yml（应用 + MySQL）、备份脚本
docs/      系统方案
```

## 部署（Docker）

```bash
cd deploy
cp .env.example .env        # 修改其中的密码和密钥
docker compose up -d --build
```

访问 `http://服务器:8000`，用 `.env` 里的管理员账号登录，首次登录强制改密码。生产环境请在前面加一层 HTTPS 反向代理（手机摄像头扫码、企微网页授权都需要 HTTPS）。

**网络要求**：服务器要同时满足两头——能访问金蝶内网地址（读物料），又能通过企业已备案的 HTTPS 域名被企微访问（`RMLM_PUBLIC_URL`，需在企微后台设为可信域名）。常见做法是应用部署在内网，由 DMZ 反向代理对外。

上线前在"账号管理"里建号并勾选角色、设置直属上级（也可 Excel 批量导入），在"主数据"里导入项目、物料（含单价）、库位和报废箱。

**备份**：`deploy/backup.sh` 每日备份数据库和照片。照片永久保存，务必纳入备份。

## 本地一键启动

前提：已安装 Python 3.11+、Node.js 18+，本机有 MySQL 8，并已建好库（默认库名 `rmlm`、账号 `rmlm`，可在 `.env` 里改）。

在仓库根目录运行（Windows、macOS、Linux 命令相同；Mac/Linux 上 `python` 可能叫 `python3`）：

```bash
python start.py            # 一键启动
```
```bash
python start.py rebuild    # 改了前端代码后：重建前端再启动
```

`start.py` 只用 Python 标准库，会自动完成：
- 首次运行时从 `.env.example` 生成 `.env`；
- 创建 Python 虚拟环境 `backend/.venv`，按需安装后端依赖（`requirements.txt` 变了才重装）；
- 前端还没构建、或指定了 `rebuild` 时，安装前端依赖并构建（`package-lock.json` 变了才重装）；
- 检查数据库能否连上、端口是否被占用，有问题给出中文提示；
- 启动服务。启动后访问 `http://localhost:8000`，手机在同一局域网内用 `http://电脑IP:8000` 访问。

数据库地址、端口、管理员账号等都在 `.env` 里改。

可选：写入演示数据（虚构人员，密码 `Demo@1234`）：

```bash
cd backend && .venv/bin/python -m scripts.demo_seed        # Windows：.venv\Scripts\python -m scripts.demo_seed
```

### 前端热更新开发（可选）

需要边改边看效果时，先用上面的脚本启动后端，再另开一个终端：

```bash
cd frontend && npm run dev            # http://localhost:5173，/api 自动代理到 8000
```

## 测试

```bash
cd backend
.venv/bin/pip install -r requirements-dev.txt
# 使用独立测试库 rmlm_test（会清空）
RMLM_DATABASE_URL="mysql+pymysql://rmlm:密码@127.0.0.1:3306/rmlm_test?charset=utf8mb4" .venv/bin/python -m pytest -q
```

## 环境变量

| 变量 | 说明 | 默认 |
|---|---|---|
| `RMLM_DATABASE_URL` | MySQL 连接串 | 本机 rmlm 库 |
| `RMLM_JWT_SECRET` | 登录令牌签名密钥 | 开发用值，生产必须修改 |
| `RMLM_JWT_HOURS` | 登录有效期（小时） | 12 |
| `RMLM_PHOTO_DIR` | 照片目录（永久保存） | `./data/photos` |
| `RMLM_PHOTO_MAX_KB` | 照片压缩目标大小 | 300 |
| `RMLM_SCHEDULER` | 是否启动定时任务（1/0） | 1 |
| `RMLM_ADMIN_USER` / `RMLM_ADMIN_PASSWORD` | 首次启动时创建的管理员 | admin / admin123 |
| `RMLM_PUBLIC_URL` | 对外访问地址（企微消息链接、网页授权回调） | 空 |
| `K3_SERVER_URL` / `K3_ACCT_ID` / `K3_USERNAME` / `K3_APP_ID` / `K3_APP_SECRET` | 金蝶云星空 WebAPI（只读）。五项都填才启用物料同步 | 空 |
| `K3_LCID` / `K3_USE_ORG` | 语言（2052 简体中文）/ 同步哪个使用组织的物料 | 2052 / 100 |
| `WECOM_CORP_ID` / `WECOM_AGENT_ID` / `WECOM_SECRET` | 企业微信自建应用。三项都填才启用企微登录和推送 | 空 |
| `WECOM_CONTACT_SECRET` / `WECOM_ROOT_DEPT` | 通讯录同步 Secret（只读，不填用应用 Secret）/ 同步的根部门 | 空 / 1 |

金蝶、企微的密钥只写在 `.env`，不要提交到仓库，也不要写进代码或脚本。

## 外部接口上线步骤

1. **金蝶**：金蝶管理员建第三方应用（只给物料等基础资料的查看权限），把账套 ID、用户名、AppID、AppSecret 填进 `.env`；重启后在"主数据 → 物料"点"从金蝶同步 → 全量"，之后每天自动增量。
2. **企业微信**：建自建应用（可见范围 = 研发部门），应用主页填 `{RMLM_PUBLIC_URL}/m`，网页授权可信域名填 `RMLM_PUBLIC_URL` 的域名，企业可信 IP 填服务器出口 IP；把企业 ID、AgentId、Secret（和通讯录同步 Secret）填进 `.env`。
3. 管理员在"账号管理"点"同步企微通讯录"：已用 Excel 导入的同名账号会自动绑定企微 UserID，新人自动开号（只有使用人角色），角色仍在系统里勾选。

