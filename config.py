# Fortigate配置文件
# 请根据实际环境修改以下配置

# Fortigate防火墙配置
FORTIGATE_IP = "10.10.0.99" # Fortigate防火墙IP地址
FORTIGATE_API_TOKEN = ""  # REST API Token

# 地址组名称
ADDRESS_GROUP_NAME = "Proxied Devices" # 策略路由地址组名称

# 计时器持续时间（秒）
TIMER_DURATION = 2 * 60 * 60  # 2小时

# 服务器监听配置
SERVER_HOST = "0.0.0.0"
SERVER_PORT = 8000

# 时区配置 (例如 "Asia/Shanghai", "UTC", "America/New_York")
TIMEZONE = "Asia/Shanghai"
