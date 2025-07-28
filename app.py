import asyncio
import uuid
import threading
import time
import os
import psutil
from datetime import datetime, timedelta
from typing import Dict, Optional
import queue
import logging
from dataclasses import dataclass
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from datetime import timezone

from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning

# 禁用SSL警告
disable_warnings(InsecureRequestWarning)

# 导入配置
try:
    from config import (
        FORTIGATE_IP, FORTIGATE_API_TOKEN,
        ADDRESS_GROUP_NAME, TIMER_DURATION, SERVER_HOST, SERVER_PORT, TIMEZONE
    )
except ImportError:
    # 如果配置文件不存在，使用默认值
    FORTIGATE_IP = "127.0.0.1"
    FORTIGATE_API_TOKEN = "123123"
    ADDRESS_GROUP_NAME = "132123"
    TIMER_DURATION = 2 * 60 * 60
    SERVER_HOST = "0.0.0.0"
    SERVER_PORT = 8000
    TIMEZONE = "UTC"  # 默认时区

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动
    await sync_from_fortigate() # 在启动时执行同步
    scheduler.start()
    logger.info("APScheduler scheduler started.")
    cleanup_thread = threading.Thread(target=cleanup_expired_objects, daemon=True)
    cleanup_thread.start()
    logger.info("Cleanup thread started.")
    yield
    # 关闭
    scheduler.shutdown()
    logger.info("APScheduler scheduler shut down.")

app = FastAPI(title="Fortigate Proxy Manager", version="1.0.0", lifespan=lifespan)

# 获取当前目录
current_dir = os.path.dirname(os.path.abspath(__file__))

# 全局变量
# active_timers: Dict[str, threading.Timer] = {}  # 不再需要
scheduler = BackgroundScheduler(timezone=TIMEZONE)
address_objects: Dict[str, str] = {}  # IP -> 地址对象名称
cleanup_queue = queue.Queue()  # 清理队列
cleanup_futures: Dict[str, asyncio.Future] = {}  # IP -> Future对象，用于异步等待清理完成
cleanup_lock = threading.Lock()  # 保证同时只处理一个清理任务
fortigate = None  # FortigateAPI实例
start_time = datetime.now()  # 服务启动时间
last_error = None  # 最后一次错误信息

@dataclass
class CleanupTask:
    """清理任务数据结构"""
    client_ip: str
    future: Optional[asyncio.Future] = None
    is_manual: bool = False  # 是否是手动断开连接

class FortigateAPI:
    def __init__(self, host: str, api_token: str):
        self.host = host
        self.api_token = api_token
        self.base_url = f"https://{host}/api/v2"
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {api_token}',
            'Content-Type': 'application/json'
        })
        self.session.verify = False  # 忽略SSL证书验证
        self.mode = "unknown"  # full, address_group_only, or unknown
        
    def test_connection(self) -> dict:
        """测试连接并检测权限模式"""
        try:
            # 测试基本连接
            response = self.session.get(f"{self.base_url}/monitor/system/status")
            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"连接失败: HTTP {response.status_code}",
                    "mode": "unknown"
                }
            
            # 测试地址对象权限
            addr_test = self.session.get(f"{self.base_url}/cmdb/firewall/address")
            addr_writable = addr_test.status_code == 200
            
            # 测试地址组权限
            group_test = self.session.get(f"{self.base_url}/cmdb/firewall/addrgrp")
            group_writable = group_test.status_code == 200
            
            # 确定模式
            if addr_writable and group_writable:
                self.mode = "full"
            elif group_writable:
                self.mode = "address_group_only"
            else:
                self.mode = "unknown"
                return {
                    "success": False,
                    "error": "权限不足：无法访问地址对象或地址组",
                    "mode": self.mode
                }
            
            return {
                "success": True,
                "mode": self.mode,
                "message": f"连接成功，模式: {self.mode}"
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"连接异常: {str(e)}",
                "mode": "unknown"
            }

    def get_all_address_objects(self) -> Optional[list]:
        """获取所有地址对象"""
        try:
            response = self.session.get(f"{self.base_url}/cmdb/firewall/address")
            if response.status_code == 200:
                return response.json().get("results", [])
            logger.error(f"获取所有地址对象失败: {response.status_code} - {response.text}")
            return None
        except Exception as e:
            logger.error(f"获取所有地址对象异常: {str(e)}")
            return None

    def get_address_group_members(self, group_name: str) -> Optional[list]:
        """获取地址组的成员列表"""
        try:
            response = self.session.get(f"{self.base_url}/cmdb/firewall/addrgrp/{group_name}")
            if response.status_code == 200:
                group_data = response.json()
                return group_data.get("results", [{}])[0].get("member", [])
            # 如果地址组不存在，返回一个明确的空列表而不是None
            if response.status_code == 404:
                logger.warning(f"地址组 {group_name} 不存在。")
                return []
            logger.error(f"获取地址组 {group_name} 成员失败: {response.status_code} - {response.text}")
            return None
        except Exception as e:
            logger.error(f"获取地址组 {group_name} 成员异常: {str(e)}")
            return None

    def create_address_object(self, name: str, ip: str) -> bool:
        """创建地址对象"""
        if self.mode == "address_group_only":
            logger.warning(f"当前模式 {self.mode} 不支持创建地址对象")
            return False
            
        data = {
            "name": name,
            "type": "ipmask",
            "subnet": f"{ip}/32",
            "comment": f"Auto-created proxy address for {ip}"
        }
        
        try:
            response = self.session.post(
                f"{self.base_url}/cmdb/firewall/address",
                json=data
            )
            
            if response.status_code in [200, 201]:
                logger.info(f"成功创建地址对象: {name}")
                return True
            else:
                logger.error(f"创建地址对象失败: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"创建地址对象异常: {str(e)}")
            return False

    def delete_address_object(self, name: str) -> bool:
        """删除地址对象"""
        if self.mode == "address_group_only":
            logger.warning(f"当前模式 {self.mode} 不支持删除地址对象")
            return False
            
        try:
            response = self.session.delete(
                f"{self.base_url}/cmdb/firewall/address/{name}"
            )
            
            if response.status_code in [200, 204]:
                logger.info(f"成功删除地址对象: {name}")
                return True
            else:
                logger.error(f"删除地址对象失败: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"删除地址对象异常: {str(e)}")
            return False

    def add_to_address_group(self, group_name: str, address_name: str) -> bool:
        """将地址对象添加到地址组"""
        try:
            # 首先获取现有的地址组配置
            response = self.session.get(
                f"{self.base_url}/cmdb/firewall/addrgrp/{group_name}"
            )
            
            if response.status_code != 200:
                logger.error(f"获取地址组 {group_name} 失败: {response.status_code}")
                return False
                
            group_data = response.json()
            current_members = group_data.get("results", [{}])[0].get("member", [])
            
            # 检查是否已经存在
            for member in current_members:
                if member.get("name") == address_name:
                    logger.info(f"地址对象 {address_name} 已在地址组 {group_name} 中")
                    return True
            
            # 添加新成员
            new_members = current_members + [{"name": address_name}]
            
            # 更新地址组
            update_data = {
                "member": new_members
            }
            
            response = self.session.put(
                f"{self.base_url}/cmdb/firewall/addrgrp/{group_name}",
                json=update_data
            )
            
            if response.status_code == 200:
                logger.info(f"成功将 {address_name} 添加到地址组 {group_name}")
                return True
            else:
                logger.error(f"添加到地址组失败: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"添加到地址组异常: {str(e)}")
            return False

    def remove_from_address_group(self, group_name: str, address_name: str) -> bool:
        """从地址组中移除地址对象"""
        try:
            # 获取现有的地址组配置
            response = self.session.get(
                f"{self.base_url}/cmdb/firewall/addrgrp/{group_name}"
            )
            
            if response.status_code != 200:
                logger.error(f"获取地址组 {group_name} 失败: {response.status_code}")
                return False
                
            group_data = response.json()
            current_members = group_data.get("results", [{}])[0].get("member", [])
            
            # 过滤掉要移除的成员
            new_members = [member for member in current_members if member.get("name") != address_name]
            
            # 更新地址组
            update_data = {
                "member": new_members
            }
            
            response = self.session.put(
                f"{self.base_url}/cmdb/firewall/addrgrp/{group_name}",
                json=update_data
            )
            
            if response.status_code == 200:
                logger.info(f"成功从地址组 {group_name} 中移除 {address_name}")
                return True
            else:
                logger.error(f"从地址组移除失败: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"从地址组移除异常: {str(e)}")
            return False

    def get_available_addresses(self) -> list:
        """获取可用的地址对象列表（用于仅地址组模式）"""
        try:
            response = self.session.get(f"{self.base_url}/cmdb/firewall/address")
            if response.status_code == 200:
                data = response.json()
                return [addr["name"] for addr in data.get("results", [])]
            return []
        except Exception as e:
            logger.error(f"获取地址对象列表失败: {str(e)}")
            return []


def cleanup_expired_objects():
    """清理过期的对象"""
    global last_error
    
    while True:
        task: Optional[CleanupTask] = None
        try:
            # 从队列中获取需要清理的对象
            task = cleanup_queue.get(timeout=1)
            if not isinstance(task, CleanupTask):
                continue
            client_ip = task.client_ip
            
            cleanup_success = False
            error_message = None
            address_name = None
            
            with cleanup_lock:
                # 计时器逻辑由APScheduler处理，这里不需要手动取消
                
                if client_ip in address_objects:
                    address_name = address_objects[client_ip]
                    
                    # 从地址组中移除
                    if fortigate:
                        success = fortigate.remove_from_address_group(ADDRESS_GROUP_NAME, address_name)
                        if success:
                            logger.info(f"已从地址组中移除 {address_name}")
                            
                            # 如果是完整模式，也删除地址对象
                            if fortigate.mode == "full":
                                delete_success = fortigate.delete_address_object(address_name)
                                if delete_success:
                                    logger.info(f"已删除地址对象 {address_name}")
                                    cleanup_success = True
                                else:
                                    logger.warning(f"删除地址对象 {address_name} 失败")
                                    cleanup_success = True  # 即使删除地址对象失败，从地址组移除成功也算成功
                            else:
                                # 仅地址组模式下，只需要从地址组移除成功即可
                                cleanup_success = True
                        else:
                            logger.error(f"从地址组移除 {address_name} 失败")
                            error_message = f"从地址组移除失败: {address_name}"
                    else:
                        error_message = "Fortigate连接不可用"
                    
                    # 从本地记录中移除
                    if cleanup_success:
                        del address_objects[client_ip]
                        logger.info(f"清理完成: {client_ip}")
                    else:
                        logger.error(f"清理失败: {client_ip} - {error_message}")
                
                # 如果有Future对象，设置结果
                if task and task.future and not task.future.done():
                    if cleanup_success:
                        # 设置成功结果
                        result = {
                            "message": "代理连接已断开",
                            "client_ip": client_ip,
                            "address_name": address_name or "unknown",
                            "cleanup_success": True
                        }
                        task.future.get_loop().call_soon_threadsafe(
                            task.future.set_result, result
                        )
                    else:
                        # 设置异常结果
                        error = HTTPException(
                            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=error_message or "清理操作失败"
                        )
                        task.future.get_loop().call_soon_threadsafe(
                            task.future.set_exception, error
                        )
                
        except queue.Empty:
            continue
        except Exception as e:
            last_error = f"清理异常: {str(e)}"
            logger.error(last_error)
            
            # 如果有Future对象，设置异常
            if task and task.future and not task.future.done():
                try:
                    error = HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"清理过程中发生异常: {str(e)}"
                    )
                    task.future.get_loop().call_soon_threadsafe(
                        task.future.set_exception, error
                    )
                except:
                    pass  # 忽略设置异常时的错误


def schedule_cleanup(client_ip: str):
    """使用 APScheduler 安排或重置清理任务"""
    run_date = datetime.now(scheduler.timezone) + timedelta(seconds=TIMER_DURATION)
    
    def cleanup_task():
        """由调度器运行的实际任务"""
        task = CleanupTask(client_ip=client_ip, is_manual=False)
        cleanup_queue.put(task)

    # 使用 client_ip 作为 job_id，如果已存在则替换
    scheduler.add_job(
        cleanup_task,
        trigger=DateTrigger(run_date=run_date),
        id=client_ip,
        name=f"Cleanup for {client_ip}",
        replace_existing=True
    )
    
    logger.info(f"已安排/重置清理任务: {client_ip}, 将在 {run_date.isoformat()} 执行")


async def sync_from_fortigate():
    """从Fortigate同步现有的代理对象"""
    global fortigate, last_error, address_objects
    logger.info("正在尝试从Fortigate同步现有的代理对象...")

    # 1. 初始化并连接
    # 使用临时变量，避免在同步失败时污染全局fortigate实例
    fgt = FortigateAPI(FORTIGATE_IP, FORTIGATE_API_TOKEN)
    test_result = fgt.test_connection()

    if not test_result["success"]:
        last_error = f"启动时同步失败: {test_result['error']}"
        logger.error(last_error)
        fortigate = None # 确保全局实例为空
        return

    fortigate = fgt # 将成功的连接赋给全局变量
    logger.info(f"启动时连接成功，模式: {fortigate.mode}")

    # 2. 仅在完整模式下执行同步
    if fortigate.mode != 'full':
        logger.warning(f"当前模式为 {fortigate.mode}，不支持对象同步。跳过同步过程。")
        return

    group_members_list = fortigate.get_address_group_members(ADDRESS_GROUP_NAME)
    all_addr_objects = fortigate.get_all_address_objects()

    if group_members_list is None or all_addr_objects is None:
        last_error = "启动时同步失败: 无法获取地址组或地址对象列表。"
        logger.error(last_error)
        return

    # 3. 创建地址名称到IP的映射
    addr_name_to_ip = {
        obj.get("name"): obj.get("subnet", "").split('/')[0]
        for obj in all_addr_objects if obj.get("subnet")
    }
    
    group_member_names = {member.get("name") for member in group_members_list}

    # 4. 识别并加载代理对象
    synced_count = 0
    for addr_name in group_member_names:
        if addr_name and addr_name.startswith("PROXY_"):
            client_ip = addr_name_to_ip.get(addr_name)
            if client_ip:
                if client_ip not in address_objects:
                    address_objects[client_ip] = addr_name
                    schedule_cleanup(client_ip)
                    logger.info(f"已同步: {addr_name} -> {client_ip}，并已安排清理任务。")
                    synced_count += 1
                else:
                    logger.warning(f"同步冲突：IP {client_ip} 已存在于本地记录中，跳过 {addr_name}。")

    logger.info(f"同步完成，共加载了 {synced_count} 个现有的代理对象。")


@app.get("/", response_class=HTMLResponse)
async def root():
    """根端点，提供前端页面"""
    try:
        with open(os.path.join(current_dir, "index.html"), "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return HTMLResponse("""
        <html>
        <head><title>Fortigate Proxy Manager</title></head>
        <body>
        <h1>Fortigate Proxy Manager</h1>
        <p>前端页面未找到，请确保 index.html 文件存在</p>
        <p><a href="/api">API 文档</a></p>
        </body>
        </html>
        """)


@app.get("/api")
async def api_info():
    """API信息端点"""
    return {
        "service": "Fortigate Proxy Manager",
        "version": "1.0.0",
        "endpoints": {
            "/connect": "Connect and create proxy address object",
            "/disconnect": "Disconnect and cleanup address object", 
            "/status": "Check connection status",
            "/health": "Health check endpoint"
        },
        "fortigate_ip": FORTIGATE_IP,
        "address_group": ADDRESS_GROUP_NAME,
        "timer_duration_hours": TIMER_DURATION / 3600
    }


@app.post("/connect")
async def connect_proxy(request: Request):
    """连接代理并创建地址对象"""
    global fortigate, last_error
    
    try:
        # 获取客户端IP
        client_ip = request.client.host if request.client else "127.0.0.1"
        
        # 如果还没有连接到Fortigate，先连接
        if not fortigate:
            fortigate = FortigateAPI(FORTIGATE_IP, FORTIGATE_API_TOKEN)
            test_result = fortigate.test_connection()
            
            if not test_result["success"]:
                last_error = test_result["error"]
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"无法连接到Fortigate: {test_result['error']}"
                )

        # 检查IP是否已经存在活动连接，如果存在则只重置计时器（续期）
        if client_ip in address_objects:
            address_name = address_objects[client_ip]
            schedule_cleanup(client_ip)  # 重置计时器
            logger.info(f"IP {client_ip} 的连接已存在，重置计时器。")
            return {
                "message": "代理连接已续期",
                "client_ip": client_ip,
                "address_name": address_name,
                "mode": fortigate.mode,
                "cleanup_in_seconds": TIMER_DURATION
            }
        
        # 生成地址对象名称，严格遵守 PROXY_uuid.uuid4() 格式
        address_name = f"PROXY_{uuid.uuid4()}"
        
        # 完整模式：创建地址对象并添加到地址组
        if fortigate.mode == "full":
            if not fortigate.create_address_object(address_name, client_ip):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="创建地址对象失败"
                )
        
        if not fortigate.add_to_address_group(ADDRESS_GROUP_NAME, address_name):
            # 如果添加到地址组失败，且是完整模式，则删除刚创建的地址对象
            if fortigate.mode == "full":
                fortigate.delete_address_object(address_name)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="添加到地址组失败"
            )
        
        # 记录地址对象 (IP -> 地址对象名称)
        address_objects[client_ip] = address_name
        
        # 安排清理任务
        schedule_cleanup(client_ip)
        
        return {
            "message": "代理连接成功",
            "client_ip": client_ip,
            "address_name": address_name,
            "mode": fortigate.mode,
            "cleanup_in_seconds": TIMER_DURATION
        }
        
    except HTTPException:
        raise
    except Exception as e:
        last_error = f"连接异常: {str(e)}"
        logger.error(last_error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"内部错误: {str(e)}"
        )

@app.post("/disconnect")
async def disconnect_proxy(request: Request):
    """断开代理连接并清理地址对象"""
    try:
        client_ip = request.client.host if request.client else "127.0.0.1"
        
        if client_ip not in address_objects:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="没有找到活动的代理连接"
            )
        
        # 从调度器中移除计划任务
        if scheduler.get_job(client_ip):
            scheduler.remove_job(client_ip)
            logger.info(f"已从调度器中移除对 {client_ip} 的清理任务。")

        # 创建Future对象等待清理完成
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        
        # 创建清理任务
        task = CleanupTask(client_ip=client_ip, future=future, is_manual=True)
        cleanup_queue.put(task)
        
        # 等待清理完成
        result = await future
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        last_error = f"断开连接异常: {str(e)}"
        logger.error(last_error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"内部错误: {str(e)}"
        )


@app.get("/status")
async def get_status(request: Request):
    """获取连接状态"""
    try:
        client_ip = request.client.host if request.client else "127.0.0.1"
        timer_remaining = None
        
        job = scheduler.get_job(client_ip)
        if job:
            # 使用调度器的时区来获取当前时间，以保证一致性
            now = datetime.now(scheduler.timezone)
            remaining = job.next_run_time - now
            timer_remaining = max(0, remaining.total_seconds())

        return {
            "connected": fortigate is not None,
            "client_ip": client_ip,
            "has_active_proxy": client_ip in address_objects,
            "host": FORTIGATE_IP,
            "address_group": ADDRESS_GROUP_NAME,
            "mode": fortigate.mode if fortigate else "unknown",
            "address_name": address_objects.get(client_ip),
            "timer_remaining": timer_remaining
        }
        
    except Exception as e:
        last_error = f"获取状态异常: {str(e)}"
        logger.error(last_error)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"内部错误: {str(e)}"
        )


@app.get("/health")
async def health():
    """健康检查端点"""
    try:
        # 获取系统信息
        process = psutil.Process()
        memory_info = process.memory_info()
        memory_usage = memory_info.rss / 1024 / 1024  # MB
        
        # 计算运行时间
        uptime = datetime.now() - start_time
        uptime_str = str(uptime).split('.')[0]  # 移除微秒
        
        return {
            "status": "healthy",
            "connected": fortigate is not None,
            "host": FORTIGATE_IP if fortigate else None,
            "mode": fortigate.mode if fortigate else "unknown",
            "active_timers": len(scheduler.get_jobs()),
            "address_objects": len(address_objects),
            "queue_size": cleanup_queue.qsize(),
            "memory_usage": memory_usage,
            "uptime": uptime_str,
            "last_error": last_error,
            "timestamp": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"健康检查异常: {str(e)}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }


if __name__ == "__main__":
    logger.info(f"启动 Fortigate Proxy Manager 服务器 {SERVER_HOST}:{SERVER_PORT}")
    logger.info(f"Fortigate: {FORTIGATE_IP}")
    logger.info(f"地址组: {ADDRESS_GROUP_NAME}")
    logger.info(f"计时器持续时间: {TIMER_DURATION}秒")
    logger.info(f"时区: {TIMEZONE}")
    
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)