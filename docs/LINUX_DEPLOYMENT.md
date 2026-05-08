# 🐧 Linux 部署完整指南

本文档提供在 Linux 系统上部署 A股自选股智能分析系统的完整指南。

## 📋 部署方案选择

| 方案 | 适用场景 | 难度 | 维护成本 |
|------|----------|------|----------|
| **Docker Compose** ⭐ | 生产环境、快速部署 | 低 | 低 |
| **直接部署** | 开发测试、定制需求 | 中 | 中 |
| **Systemd 服务** | 长期运行、开机自启 | 高 | 低 |
| **GitHub Actions** | 无服务器、自动化 | 低 | 无 |

---

## 🐳 方案一：Docker Compose 部署（推荐）

### 系统要求
- Ubuntu 18.04+ / CentOS 7+ / Debian 9+
- Docker 20.03+
- Docker Compose 1.29+

### 安装步骤

#### 1. 安装 Docker
```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# CentOS/RHEL
sudo yum install -y yum-utils
sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo yum install -y docker-ce docker-ce-cli containerd.io
sudo systemctl start docker
sudo systemctl enable docker

# 验证安装
docker --version
docker-compose --version
```

#### 2. 部署应用
```bash
# 克隆项目
git clone <your-repo-url> /opt/stock-analyzer
cd /opt/stock-analyzer

# 配置环境变量
cp .env.example .env
vim .env  # 必填：API_KEY, STOCK_LIST, 通知渠道

# 启动服务
docker-compose -f ./docker/docker-compose.yml up -d

# 查看状态
docker-compose -f ./docker/docker-compose.yml ps
docker-compose -f ./docker/docker-compose.yml logs -f
```

#### 3. 验证部署
```bash
# 检查服务状态
curl http://localhost:8000/api/health

# 手动执行分析
docker-compose -f ./docker/docker-compose.yml exec stock-analyzer python main.py --no-notify
```

### 常用管理命令
```bash
# 停止服务
docker-compose -f ./docker/docker-compose.yml down

# 重启服务
docker-compose -f ./docker/docker-compose.yml restart

# 更新部署
git pull
docker-compose -f ./docker/docker-compose.yml build --no-cache
docker-compose -f ./docker/docker-compose.yml up -d

# 进入容器调试
docker-compose -f ./docker/docker-compose.yml exec stock-analyzer bash

# 查看日志
docker-compose -f ./docker/docker-compose.yml logs -f --tail=100
```

---

## 🖥️ 方案二：直接部署

### 系统要求
- Python 3.10+
- 2GB+ RAM
- 1GB+ 磁盘空间

### 安装步骤

#### 1. 安装 Python 环境
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install -y python3.10 python3.10-venv python3-pip python3.10-dev

# CentOS/RHEL
sudo yum install -y python3.10 python3-pip python3.10-devel

# 验证版本
python3.10 --version
```

#### 2. 创建应用目录
```bash
sudo mkdir -p /opt/stock-analyzer
sudo chown $USER:$USER /opt/stock-analyzer
cd /opt/stock-analyzer
```

#### 3. 部署应用
```bash
# 克隆代码
git clone <your-repo-url> .

# 创建虚拟环境
python3.10 -m venv venv
source venv/bin/activate

# 安装依赖
pip install --upgrade pip
pip install -r requirements.txt

# 配置环境
cp .env.example .env
vim .env

# 测试运行
python main.py --dry-run
```

#### 4. 运行应用
```bash
# 单次分析
python main.py

# 定时任务（前台）
python main.py --schedule

# 后台运行
nohup python main.py --schedule > /dev/null 2>&1 &

# 启动 Web 界面
python main.py --webui-only
```

---

## ⚙️ 方案三：Systemd 服务

### 创建服务文件
```bash
sudo vim /etc/systemd/system/stock-analyzer.service
```

### 服务配置
```ini
[Unit]
Description=A股自选股智能分析系统
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/stock-analyzer
Environment="PATH=/opt/stock-analyzer/venv/bin"
ExecStart=/opt/stock-analyzer/venv/bin/python main.py --schedule
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### 启动管理
```bash
# 重载配置
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start stock-analyzer

# 开机自启
sudo systemctl enable stock-analyzer

# 查看状态
sudo systemctl status stock-analyzer

# 查看日志
sudo journalctl -u stock-analyzer -f

# 停止服务
sudo systemctl stop stock-analyzer

# 重启服务
sudo systemctl restart stock-analyzer
```

---

## 🔧 环境配置详解

### 必须配置项
```bash
# .env 文件配置示例
# AI 模型（至少配置一个）
ANSPIRE_API_KEYS=your_anspire_key
# 或
GEMINI_API_KEY=your_gemini_key

# 股票列表
STOCK_LIST=600519,000001,300750

# 通知渠道（至少配置一个）
WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
# 或
EMAIL_SENDER=your_email@qq.com
EMAIL_PASSWORD=your_authorization_code
```

### 可选配置项
```bash
# 定时任务
SCHEDULE_ENABLED=true
SCHEDULE_TIME=18:00

# 服务器配置
WEBUI_HOST=0.0.0.0
API_PORT=8000

# 数据源配置
TUSHARE_TOKEN=your_tushare_token
SERPAPI_API_KEYS=your_serpapi_key
```

---

## 🌐 代理配置

### Docker 部署代理
```bash
# 编辑 docker-compose.yml
environment:
  - http_proxy=http://proxy-server:port
  - https_proxy=http://proxy-server:port
  - no_proxy=localhost,127.0.0.1
```

### 直接部署代理
```bash
# 在 .env 文件中添加
http_proxy=http://proxy-server:port
https_proxy=http://proxy-server:port
no_proxy=localhost,127.0.0.1

# 或在 shell 中设置
export http_proxy=http://proxy-server:port
export https_proxy=http://proxy-server:port
```

---

## 📊 监控与维护

### 日志管理
```bash
# Docker 部署日志
docker-compose -f ./docker/docker-compose.yml logs -f --tail=100

# 直接部署日志
tail -f /opt/stock-analyzer/logs/stock_analysis_*.log

# Systemd 服务日志
sudo journalctl -u stock-analyzer -f
```

### 健康检查
```bash
# 检查进程
ps aux | grep main.py

# 检查端口
netstat -tlnp | grep 8000

# 检查服务状态
curl http://localhost:8000/api/health
```

### 数据备份
```bash
# 备份数据
tar -czvf stock-analyzer-backup-$(date +%Y%m%d).tar.gz \
  .env data/ logs/ reports/

# 自动备份脚本
cat > /opt/stock-analyzer/backup.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="/opt/backups/stock-analyzer"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p $BACKUP_DIR
tar -czvf $BACKUP_DIR/backup-$DATE.tar.gz .env data/ logs/ reports/
find $BACKUP_DIR -name "*.tar.gz" -mtime +7 -delete
EOF

chmod +x /opt/stock-analyzer/backup.sh

# 添加到 crontab（每日凌晨2点备份）
echo "0 2 * * * /opt/stock-analyzer/backup.sh" | sudo crontab -
```

---

## 🔍 故障排除

### 常见问题

#### 1. Docker 构建失败
```bash
# 清理缓存重新构建
docker system prune -f
docker-compose -f ./docker/docker-compose.yml build --no-cache
```

#### 2. API 访问超时
```bash
# 检查网络连接
curl -I https://api.openai.com

# 检查代理配置
echo $http_proxy
echo $https_proxy
```

#### 3. 数据库锁定
```bash
# 停止服务
docker-compose -f ./docker/docker-compose.yml down

# 删除锁文件
rm -f /opt/stock-analyzer/data/*.lock

# 重启服务
docker-compose -f ./docker/docker-compose.yml up -d
```

#### 4. 内存不足
```bash
# 检查内存使用
free -h
docker stats

# 调整 Docker 内存限制
# 编辑 docker-compose.yml
deploy:
  resources:
    limits:
      memory: 1G
```

#### 5. 端口被占用
```bash
# 查找占用端口的进程
sudo lsof -i :8000

# 杀死进程
sudo kill -9 <PID>

# 或修改端口
# 在 .env 中设置 API_PORT=8001
```

---

## 🚀 性能优化

### 系统优化
```bash
# 增加文件描述符限制
echo "* soft nofile 65536" | sudo tee -a /etc/security/limits.conf
echo "* hard nofile 65536" | sudo tee -a /etc/security/limits.conf

# 优化内核参数
echo "net.core.somaxconn = 1024" | sudo tee -a /etc/sysctl.conf
echo "net.ipv4.tcp_max_syn_backlog = 1024" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

### Docker 优化
```bash
# 清理未使用的镜像和容器
docker system prune -a -f

# 设置日志轮转
# 编辑 /etc/docker/daemon.json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}

sudo systemctl restart docker
```

---

## 📈 监控告警

### Prometheus + Grafana（可选）
```bash
# 安装 node_exporter
docker run -d --name node_exporter \
  -p 9100:9100 \
  prom/node-exporter

# 配置 prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'stock-analyzer'
    static_configs:
      - targets: ['localhost:8000']
```

### 简单监控脚本
```bash
cat > /opt/stock-analyzer/monitor.sh << 'EOF'
#!/bin/bash
# 检查服务状态
if ! curl -f http://localhost:8000/api/health > /dev/null 2>&1; then
    echo "Stock analyzer service is down!" | mail -s "Alert" admin@example.com
    systemctl restart stock-analyzer
fi
EOF

chmod +x /opt/stock-analyzer/monitor.sh

# 每5分钟检查一次
echo "*/5 * * * * /opt/stock-analyzer/monitor.sh" | sudo crontab -
```

---

## 🔄 迁移与升级

### 数据迁移
```bash
# 源服务器打包
cd /opt/stock-analyzer
tar -czvf stock-analyzer-migrate.tar.gz .env data/ logs/ reports/

# 目标服务器恢复
mkdir -p /opt/stock-analyzer
cd /opt/stock-analyzer
tar -xzvf stock-analyzer-migrate.tar.gz
```

### 版本升级
```bash
# Docker 升级
git pull
docker-compose -f ./docker/docker-compose.yml down
docker-compose -f ./docker/docker-compose.yml build --no-cache
docker-compose -f ./docker/docker-compose.yml up -d

# 直接部署升级
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart stock-analyzer
```

---

## 📞 技术支持

### 日志位置
- Docker: `docker-compose logs`
- 直接部署: `/opt/stock-analyzer/logs/`
- Systemd: `journalctl -u stock-analyzer`

### 配置文件
- 主配置: `/opt/stock-analyzer/.env`
- 数据目录: `/opt/stock-analyzer/data/`
- 报告目录: `/opt/stock-analyzer/reports/`

### 常用命令快速参考
```bash
# 查看服务状态
docker-compose ps  # Docker
systemctl status stock-analyzer  # Systemd

# 查看日志
docker-compose logs -f  # Docker
journalctl -u stock-analyzer -f  # Systemd

# 重启服务
docker-compose restart  # Docker
systemctl restart stock-analyzer  # Systemd
```

---

## 🔗 相关文档

- [部署指南](DEPLOY.md) - 详细的部署说明
- [完整配置指南](full-guide.md) - 高级配置选项
- [桌面端打包说明](desktop-package.md) - 桌面应用部署
- [FAQ](FAQ.md) - 常见问题解答

---

**部署完成后，访问 http://your-server:8000 即可使用 Web 管理界面！**
