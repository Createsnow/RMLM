# 研发物料管家 RMLM

研发物料全生命周期管理系统：申请 → 直属上级审核 → ERP 出库 → 本人签收 → 使用 / 损耗 / 拆分 → 暂存 / 转交 → 退库 / 报废 → 对账关账。

- 后端：Python 3.13 + FastAPI + SQLAlchemy + **MySQL 8**（不依赖 Redis；定时任务进程内运行，多实例用 MySQL 命名锁防重）
- 前端：Vue 3 + Element Plus，一个网站，电脑和手机都能用：按屏幕宽度自动进电脑界面或手机界面（`/m`），两边可切换
- 金蝶云星空：**只读**，中台不回写金蝶。物料档案通过 WebAPI 每天增量同步（也可手动）；研发领料 = 金蝶其他出库单（`STK_MisDelivery`）中单据类型为"研发测试其他出库单""客供领料其他出库单"的单据（`K3_LL_BILL_TYPES`），按网页「ERP 单据 → 自动同步设置」的间隔增量拉取（管理员可开关、选 15 分钟～24 小时，默认每 15 分钟；首次同步默认只拉最近 3 个月），过账后按备注里的申请号自动生成领用行，使用人直接签收；退料 / 报废单不批量拉，领料专员分摊或核销时输入金蝶单号按单取回（其他入库 / 其他出库单均可，不区分单据类型）；A 转 B、调整单一期不接，手工录入 / Excel 导入；项目档案暂用 Excel 导入
- 企业微信：手机端在企微工作台打开、网页授权免密登录；通讯录每天同步人员和直属上级；站内待办同步推送企微应用消息。未配置企微时自动退回账号密码登录 + 站内待办
- 仓储系统：实物条码由仓储统一赋码，签收时扫仓储条码绑定领用行（一个码一条行），拆分时向仓储申请新码，接口见 [docs/仓储接口规范.md](docs/仓储接口规范.md)。未配置时本系统发 RM 临时码（仅供开发、测试、演示）。扫码支持 PDA 扫码枪（键盘输入）和手机摄像头

- 旧台账：一次导入为只读**历史台账**（基础数据 → 历史台账；业务蓝图称"历史基线"），只用于查询与总量核对，不进台账、不参与对账与考核
- 双轨并行期：对账与报表 → 统计报表 → 双轨抽查，每周抽 20 单与旧表比对，连续两周一致率 ≥ 95% 可进入收口

设计说明与已确认的业务规则见 [docs/系统方案.md](docs/系统方案.md)，功能模块划分与界面用语见 [docs/术语表.md](docs/术语表.md)，总体架构图见 [docs/RMLM 总体架构.html](docs/RMLM%20总体架构.html)；Docker Compose 部署与日常运维（部署步骤、启停、定时任务、外部接口、巡检、备份恢复、升级回退、故障处理）见 [docs/运维手册.md](docs/运维手册.md)。

## 目录

```
backend/            FastAPI 应用（app/）、测试（tests/）、演示数据脚本（scripts/demo_seed.py）
frontend/           Vue 3 前端；Dockerfile + nginx.conf（web 镜像）
docs/               系统方案、术语表、仓储接口规范、总体架构图、运维手册
deploy/backup.sh    Docker 部署的每日备份脚本
Dockerfile          api 镜像
docker-compose.yml  api + web（数据库用公司 MySQL，不随附）
.env.example        配置模板：本机运行与 Docker 部署共用一份 .env
```

## 部署（Docker）

完整的分步部署与运维说明见 [docs/运维手册.md](docs/运维手册.md)，下面是快速版。

两个容器：**web**（nginx，对外提供页面，并把 `/api` 转发给 api）和 **api**（FastAPI，不对宿主机开放端口）。数据库用公司已有的 MySQL 8，不在 Docker 里。

在仓库根目录：

```bash
cp .env.example .env        # 按下面的清单修改
```
```bash
docker compose up -d --build
```

`.env` 上线前至少改这几项：

- `DB_HOST` 等数据库连接：写公司 MySQL 服务器的 IP 或域名（不能写 127.0.0.1，那是容器自己）。库要提前建好（utf8mb4），表在首次启动时自动建。
- `RMLM_JWT_SECRET`（至少 16 位随机字符）、`RMLM_ADMIN_PASSWORD`。
- 金蝶 `K3_*`、企微 `WECOM_*`、`RMLM_PUBLIC_URL`、仓储 `WMS_*` 按需填写。

访问 `http://服务器:8000`（端口改 `.env` 的 `WEB_PORT`），用 `.env` 里的管理员账号登录，登录后请在左侧栏左下角的当前账号菜单里点“修改密码”，改掉初始密码（系统不强制改密码）。生产环境请在前面加一层 HTTPS 反向代理（手机摄像头扫码、企微网页授权都需要 HTTPS）。

常用命令（都在仓库根目录执行）：

| 操作 | 命令 |
|---|---|
| 更新代码后重新部署 | `git pull` 后 `docker compose up -d --build` |
| 看状态 / 健康检查 | `docker compose ps`（api 的 `/health/ready` 会检查数据库连接） |
| 看日志 | `docker compose logs -f api` |
| 停止 | `docker compose down`（数据在数据卷里，不会删除） |
| 写入演示数据 | `docker compose exec api python -m scripts.demo_seed` |

容器日志按 `LOG_MAX_SIZE` × `LOG_MAX_FILE` 轮转；后端进程数 `RMLM_WORKERS`；国内服务器构建慢时设 `NPM_REGISTRY` / `PIP_INDEX_URL` 镜像源。

**网络要求**：服务器要同时满足两头——能访问金蝶内网地址（读物料），又能通过企业已备案的 HTTPS 域名被企微访问（`RMLM_PUBLIC_URL`，需在企微后台设为可信域名）。常见做法是应用部署在内网，由 DMZ 反向代理对外。

上线前在"系统管理 → 用户管理"里建号并勾选角色、设置直属上级（也可 Excel 批量导入），在"基础数据 → 基础资料"里导入项目、物料（含单价）、库位和报废箱。

**备份**：`deploy/backup.sh` 每日备份数据库和照片（直连公司 MySQL 导出，连接参数取自运行中的 api 容器；公司 MySQL 已统一备份时用 `SKIP_DB=1` 只备份照片）。照片永久保存，务必纳入备份。

## 本地一键启动

前提：已安装 Python 3.11+、Node.js 18+，有可连接的 MySQL 8，并已建好库（默认本机、库名 `rmlm`、账号 `rmlm`，在 `.env` 的 `DB_HOST` 等项里改）。

在仓库根目录运行（Windows、macOS、Linux 命令相同；Mac/Linux 上 `python` 可能叫 `python3`）：

```bash
python start.py            # 一键启动
```
```bash
python start.py rebuild    # 改了前端代码后：重建前端再启动
```

按 **Ctrl+C** 停止（后台运行时用 `kill <进程号>`）：会等正在处理的请求结束，最多约 15 秒。

`start.py` 只用 Python 标准库，会自动完成：
- 首次运行时从 `.env.example` 生成 `.env`；
- 创建 Python 虚拟环境 `backend/.venv`，按需安装后端依赖（`requirements.txt` 变了才重装）；
- 前端还没构建、或指定了 `rebuild` 时，安装前端依赖并构建（`package-lock.json` 变了才重装）；
- 检查数据库能否连上、端口是否被占用，有问题给出中文提示；
- 启动服务。启动后访问 `http://localhost:8000`，手机在同一局域网内用 `http://电脑IP:8000` 访问。

数据库（`DB_*`）、金蝶连接（`K3_*`）、管理员账号等都在 `.env` 里改。

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
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | MySQL 连接（分项填写，密码可含特殊字符） | 127.0.0.1 / 3306 / rmlm / rmlm / rmlm_dev |
| `RMLM_DATABASE_URL` | 完整 MySQL 连接串；设置后优先于上面的 `DB_*` | 空 |
| `RMLM_JWT_SECRET` | 登录令牌签名密钥：至少 16 位随机字符；为空、仍是示例值或过短时**拒绝启动**（本地 `start.py` 会自动生成） | 无 |
| `RMLM_JWT_HOURS` | 登录有效期（小时） | 12 |
| `RMLM_OVER_ISSUE_PCT` | 开单数量允许超出申请数量的比例（%），0 = 不允许超出 | 0 |
| `RMLM_PHOTO_DIR` | 照片目录（永久保存） | `./data/photos` |
| `RMLM_PHOTO_MAX_KB` | 照片压缩目标大小 | 300 |
| `RMLM_WATERMARK_FONT` | 照片水印的中文字体文件 | 空：依次找 Linux 文泉驿、Windows 微软雅黑 / 黑体、Mac 苹方 / 黑体 |
| `RMLM_SCHEDULER` | 是否启动定时任务（1/0） | 1 |
| `RMLM_ADMIN_USER` / `RMLM_ADMIN_PASSWORD` | 首次启动时创建的管理员 | admin / admin123 |
| `RMLM_PUBLIC_URL` | 对外访问地址（企微消息链接、网页授权回调） | 空 |
| `K3_SERVER_URL` / `K3_ACCT_ID` / `K3_USERNAME` / `K3_APP_ID` / `K3_APP_SECRET` | 金蝶云星空 WebAPI（只读）。五项都填才启用物料同步 | 空 |
| `K3_LCID` / `K3_USE_ORG` | 语言（2052 简体中文）/ 同步哪个组织的物料和单据（物料按使用组织，单据按库存组织） | 2052 / 100 |
| `K3_LL_BILL_TYPES` | 研发领料在其他出库单里的单据类型（名称或编码，逗号分隔）；只自动拉这些类型 | 研发测试其他出库单,客供领料其他出库单 |
| `K3_SYNC_START_DATE` / `K3_SYNC_START_MONTHS` | 领料单首次同步（及手动全量同步）的起始日期：只拉修改日期在此之后的单据；固定日期优先，不填为今天往前 N 个月。之后按网页设置的间隔增量（默认每 15 分钟） | 空 / 3 |
| `K3_LOGIN_TIMEOUT` / `K3_QUERY_TIMEOUT` / `K3_VIEW_TIMEOUT` | 金蝶登录 / 查询 / 查看单据超时（秒）；旧的 `K3_TIMEOUT` 仍可用，作查询超时默认值 | 30 / 120 / 60 |
| `K3_PAGE_RETRIES` / `K3_PAGE_RETRY_SECONDS` | 金蝶分页查询失败（超时、断连、5xx）重试次数 / 间隔（秒） | 3 / 10 |
| `WECOM_CORP_ID` / `WECOM_AGENT_ID` / `WECOM_SECRET` | 企业微信自建应用。三项都填才启用企微登录和推送 | 空 |
| `WECOM_CONTACT_SECRET` / `WECOM_ROOT_DEPT` | 通讯录同步 Secret（只读；不填用应用 Secret，按部门读取应用可见范围内的成员）/ 同步的根部门 | 空 / 1 |
| `WMS_BASE_URL` / `WMS_TOKEN` | 仓储系统接口地址 / 令牌。两项都填才启用（签收扫仓储条码、拆分向仓储申请新码） | 空 |
| `WMS_TIMEOUT` | 调用仓储接口的超时（秒） | 10 |

金蝶、企微、仓储的密钥只写在 `.env`，不要提交到仓库，也不要写进代码或脚本。

## 外部接口上线步骤

1. **金蝶**：金蝶管理员建第三方应用（只给物料等基础资料的查看权限），把账套 ID、用户名、AppID、AppSecret 填进 `.env`；重启后在"基础资料 → 物料"点"从金蝶同步 → 全量"，之后每天自动增量。
2. **企业微信**：建自建应用（可见范围 = 研发部门），应用主页填 `{RMLM_PUBLIC_URL}/`（企微只是入口：点开就是 RMLM 网页并免密登录，手机企微、电脑版企微都可用，系统按屏幕自动选界面），网页授权可信域名填 `RMLM_PUBLIC_URL` 的域名，企业可信 IP 填服务器出口 IP；把企业 ID、AgentId、Secret（和通讯录同步 Secret）填进 `.env`。
3. 管理员在"用户管理"点"同步企微通讯录"：已用 Excel 导入的同名账号会自动绑定企微 UserID，新人自动开号（只有使用人角色），角色仍在系统里勾选。
4. **仓储系统**：把 [docs/仓储接口规范.md](docs/仓储接口规范.md) 交给仓储系统实现两个接口；拿到地址和令牌后填 `WMS_BASE_URL`、`WMS_TOKEN`，按规范第六节联调。启用后签收必须扫仓储条码。
