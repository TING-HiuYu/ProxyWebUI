# Fortigate Proxy Manager

Fortigate Proxy Manager 是一个基于 FastAPI 的 Web 服务，用于通过 Fortigate 防火墙 API 动态管理代理地址对象和地址组，搭配策略路由和Passwall可以实现局域网用户通过连接梯子。如果有需求也可以自己修改代码以支持其他的路由器（只要是支持REST API的都可以的），关键是找到接口和返回的数据结构。

## 功能特性
- 一键添加/移除 Fortigate 地址对象到指定地址组
- 支持定时自动清理过期对象
- 支持 APScheduler 定时任务
- 提供简单易用的 Web 前端页面
- RESTful API 接口，便于集成
- 启动时自动同步 Fortigate 现有代理对象

## 目录结构
```
app.py              # 主应用逻辑（FastAPI 服务）
config.py           # 配置文件（需根据实际环境修改）
index.html          # 前端页面（可直接访问）
requirements.txt    # Python 依赖包列表
```

## 快速开始
### 1. 安装依赖
```bash
pip install -r requirements.txt
```

### 2. 配置参数
编辑 `config.py`，填写 Fortigate 的 IP、API Token、地址组名称等信息：
```python
FORTIGATE_IP = "your_fortigate_ip"           # Fortigate防火墙IP地址
FORTIGATE_API_TOKEN = "your_token"    # REST API Token
ADDRESS_GROUP_NAME = "Proxied Devices" # 策略路由地址组名称,也可以自行更改
TIMER_DURATION = 2 * 60 * 60           # 计时器持续时间（秒）
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8000
TIMEZONE = "Asia/Shanghai"
```

### 3. 启动服务
```bash
python app.py
```

### 4. 访问前端页面
浏览器访问 [http://localhost:8000/](http://localhost:8000/) 即可使用。

## API 说明
- `POST /connect`    ：添加本机 IP 到 Fortigate 地址组
- `POST /disconnect` ：从地址组移除本机 IP
- `GET /status`      ：查询当前连接状态
- `GET /health`      ：健康检查
- `GET /api`         ：API 信息

## 依赖环境
- Python 3.8+
- FastAPI
- Uvicorn
- requests
- APScheduler
- 其他依赖见 `requirements.txt`

## 注意事项
- 需在 Fortigate 上提前创建 API Token，并赋予相应权限
- 地址组需提前在 Fortigate 上创建
- 生产环境建议使用 HTTPS 部署
- 默认忽略 SSL 证书验证（如有安全要求请修改）

## License
MIT
