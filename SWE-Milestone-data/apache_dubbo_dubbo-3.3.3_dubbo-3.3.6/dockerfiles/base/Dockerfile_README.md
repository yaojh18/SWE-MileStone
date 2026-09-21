# Base Dockerfile 逐行指令说明

## 概述
此 Dockerfile 用于构建 Apache Dubbo 项目的基础测试环境镜像，包含 JDK 21、Maven、Git 等工具，以及预下载的 Zookeeper 和项目依赖。

---

## 逐行说明

### 第1行
```dockerfile
FROM maven:3.9-eclipse-temurin-21
```
**说明**: 使用 Maven 3.9 + Eclipse Temurin JDK 21 作为基础镜像。Temurin 是 Eclipse 基金会提供的开源 JDK 发行版。

---

### 第4-7行
```dockerfile
RUN apt-get update && apt-get install -y \
    git \
    wget \
    && rm -rf /var/lib/apt/lists/*
```
**说明**: 更新 apt 包索引并安装必要的工具：
- `git`: 版本控制工具，用于管理代码仓库
- `wget`: 下载工具，用于下载 Zookeeper
- 最后清理 apt 缓存以减小镜像体积

---

### 第10行
```dockerfile
RUN git config --global --add safe.directory /testbed
```
**说明**: 配置 Git 将 `/testbed` 目录标记为安全目录，允许在该目录下执行 Git 操作（解决 Git 安全限制问题）。

---

### 第13行
```dockerfile
WORKDIR /testbed
```
**说明**: 设置工作目录为 `/testbed`，后续所有命令都在此目录下执行。

---

### 第16行
```dockerfile
COPY . /testbed/
```
**说明**: 将当前构建上下文（Dubbo 仓库）的所有内容复制到容器的 `/testbed` 目录。

---

### 第19行
```dockerfile
RUN git checkout 8e49668a45
```
**说明**: 切换到基础 SHA 提交 `8e49668a45`，这是所有里程碑的起始点（对应 Dubbo 3.3.3 版本）。

---

### 第22行
```dockerfile
ENV MAVEN_OPTS="-XX:+UseG1GC -XX:InitiatingHeapOccupancyPercent=45 ..."
```
**说明**: 设置 Maven JVM 参数以优化构建性能：
- `UseG1GC`: 使用 G1 垃圾回收器
- `InitiatingHeapOccupancyPercent=45`: GC 触发阈值
- `UseStringDeduplication`: 字符串去重，节省内存
- `TieredCompilation` 相关: 禁用分层编译以加快启动
- `maven.javadoc.skip=true`: 跳过 Javadoc 生成
- 网络重试和连接超时配置

---

### 第25-29行
```dockerfile
RUN mkdir -p /testbed/.tmp/zookeeper && \
    (wget -c https://archive.apache.org/dist/zookeeper/... || ...)
```
**说明**: 下载 Zookeeper 3.7.2 二进制包（测试需要）：
- 创建临时目录存放下载文件
- 尝试从多个镜像源下载，提高下载成功率
- 使用 `-c` 参数支持断点续传

---

### 第32行
```dockerfile
RUN mvn dependency:go-offline -B || true
```
**说明**: 预下载项目所有依赖到本地 Maven 仓库：
- `dependency:go-offline`: 下载项目依赖供离线使用
- `-B`: 批处理模式（非交互式）
- `|| true`: 即使失败也继续（部分依赖可能无法下载）

---

### 第35行
```dockerfile
RUN mvn clean compile test-compile -DskipTests -B -Pskip-spotless -Dcheckstyle.skip=true -Drat.skip=true
```
**说明**: 编译项目源代码和测试代码：
- `clean`: 清理之前的构建产物
- `compile`: 编译主代码
- `test-compile`: 编译测试代码
- `-DskipTests`: 跳过测试执行
- `-Pskip-spotless`: 跳过代码格式检查
- `-Dcheckstyle.skip=true`: 跳过 Checkstyle 检查
- `-Drat.skip=true`: 跳过 Apache RAT 许可证检查

---

### 第38-39行
```dockerfile
ENV DISABLE_FILE_SYSTEM_TEST=true
ENV PYTHONUNBUFFERED=1
```
**说明**:
- 禁用文件系统相关测试（可能在容器环境中不稳定）
- 设置 Python 输出为无缓冲模式，确保日志实时输出
