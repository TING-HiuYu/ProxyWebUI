# Proxy WebUI

Proxy WebUI 是一个基于 FastAPI 的 Web 服务，用于通过 Fortigate 防火墙 API 动态管理代理地址对象和地址组，搭配策略路由和OpenWRT可以实现局域网用户通过连接梯子。如果有需求也可以自己修改代码以支持其他的路由器（只要是支持REST API的都可以的），关键是找到接口和返回的数据结构。

客户端样式Demo:  
![501753732604_ pic](https://github.com/user-attachments/assets/1d265427-a815-4584-a80f-76e5fe7af7a4)
![511753732605_ pic](https://github.com/user-attachments/assets/770c6900-ad4e-4096-9903-5c8a876b6ef5)


## 实现原理
其实非常简单，就是在通过web服务器读取客户端源地址，然后添加该地址对象进策略路由作用的源地址组里面。

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
## 系统需求
- 一台Fortigate防火墙作为出口防火墙部署
- 一台有Python 3.11环境的服务器，推荐使用Debian安装宝塔面板进行管理

## 部署指南

### 1. Fortigate 配置用户组
如图添加用户组:  
<img width="358" height="270" alt="截屏2025-07-29 03 24 53" src="https://github.com/user-attachments/assets/a6051c3f-63d2-4c16-bfe5-2f0c1c61033f" />

### 2. Fortigate 配置一个策略路由
如图配置策略路由:  
<img width="475" height="504" alt="截屏2025-07-29 03 17 18" src="https://github.com/user-attachments/assets/67d16d5f-fd53-4ca9-a944-c357e0b869a5" />

### 3. Fortigate 配置一个REST API用户
权限节点如下:  
<img width="320" height="546" alt="截屏2025-07-29 03 29 35" src="https://github.com/user-attachments/assets/6d8c7867-6cbf-4ce5-bef6-31ad8be7189b" />

### 4. 下载python文件到服务器
对于宝塔面板，可以在电脑上下载然后直接上传所有文件到服务器  
纯CLI，请运行
```bash
git clone https://github.com/TING-HiuYu/ProxyWebUI


```

### 5. 服务器上安装依赖
```bash
pip install virtualenv 
python -m venv .venv
#以上两步如果有现成的虚拟环境可以忽略

source ./.venv/bin/activate
pip install -r requirements.txt


```

### 6. 服务器上配置参数
编辑 `config.py`，填写 Fortigate 的 IP、API Token、地址组名称等信息：
```python
FORTIGATE_IP = "your_fortigate_ip"           # Fortigate防火墙IP地址
FORTIGATE_API_TOKEN = "your_token"    # REST API Token
ADDRESS_GROUP_NAME = "Proxied Devices" # 策略路由地址组名称,也可以自行更改
TIMER_DURATION = 2 * 60 * 60           # 计时器持续时间（秒）
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8000 #运行端口，可以自行修改
TIMEZONE = "Asia/Shanghai"
```

### 7. 启动服务
```bash
source ./.venv/bin/activate #如果没有激活虚拟环境
python app.py
```

### 8. 访问前端页面
浏览器访问 [http://localhost:8000/](http://localhost:8000/) 即可使用。

## API 说明
- `POST /connect`    ：添加本机 IP 到 Fortigate 地址组
- `POST /disconnect` ：从地址组移除本机 IP
- `GET /status`      ：查询当前连接状态
- `GET /health`      ：健康检查
- `GET /api`         ：API 信息

## 注意事项
- 需在 Fortigate 上提前创建 API Token，并赋予相应权限
- 地址组需提前在 Fortigate 上创建
- 生产环境建议使用 HTTPS 部署
- 默认忽略 SSL 证书验证（如有安全要求请自行修改代码）
- README文件和很多注释都是AI生成，有不明白的请提issue

## License
MIT
