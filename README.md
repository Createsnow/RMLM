# 研发物料管家 RMLM

研发物料全生命周期管理系统：申请 → 直属上级审核 → ERP 出库 → 本人签收 → 使用 / 损耗 / 拆分 → 暂存 / 转交 → 退库 / 报废 → 对账关账。

- 后端：Python 3.13 + FastAPI + SQLAlchemy + **MySQL 8**（不依赖 Redis；定时任务进程内运行，多实例用 MySQL 命名锁防重）
- 前端：Vue 3 + Element Plus，一套代码同时提供电脑端管理台和手机 H5（`/m`）
- 金蝶：**只读**。单据以"镜像"方式进入中台（手工录入 / Excel 导入金蝶导出），中台不回写金蝶；预留只读接口 `ErpReader`
- 不涉及企业微信：账号密码登录，站内待办与通知；扫码支持 PDA 扫码枪（键盘输入）和手机摄像头

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

访问 `http://服务器:8000`，用 `.env` 里的管理员账号登录，首次登录强制改密码。生产环境请在前面加一层 HTTPS 反向代理（手机摄像头扫码需要 HTTPS）。

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
