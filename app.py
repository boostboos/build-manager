# Copyright 2026 boostboos
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
import subprocess
import threading
import gzip
import time
import logging
from shutil import which
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import desc
import requests

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build_manager")

app = Flask(__name__)
secret_key = os.environ.get("FLASK_SECRET_KEY") or os.environ.get("SECRET_KEY")
if secret_key:
    app.config["SECRET_KEY"] = secret_key
else:
    log.warning("SECURITY: 未设置 FLASK_SECRET_KEY 环境变量，使用不安全的默认密钥")
    app.config["SECRET_KEY"] = "change-me"

if os.environ.get("FLASK_DEBUG") == "1":
    log.warning("SECURITY: FLASK_DEBUG=1 已开启，请勿在生产环境使用")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(BASE_DIR, 'build_manager.db')}"
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
db = SQLAlchemy(app)

_live_logs = {}
_live_logs_lock = threading.Lock()
_running_processes = {}
_running_processes_lock = threading.Lock()
_build_cancelled = set()
_build_cancelled_lock = threading.Lock()


class Project(db.Model):
    __tablename__ = "projects"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    description = db.Column(db.Text, default="")
    project_root = db.Column(db.String(500), nullable=False)
    maven_command = db.Column(db.String(500), default="")
    maven_settings = db.Column(db.String(500), default="")
    maven_java_home = db.Column(db.String(500), default="")
    maven_profile = db.Column(db.String(100), default="")
    maven_offline = db.Column(db.Boolean, default=False)
    dockerfile_dir = db.Column(db.String(500), default="docker")
    dockerfile_name = db.Column(db.String(100), default="DockerfileH")
    image_tag_prefix = db.Column(db.String(300), default="registry.example.com/my-project/")
    image_tag_suffix = db.Column(db.String(100), default="")
    compress = db.Column(db.String(10), default="Gzip")
    no_cache = db.Column(db.Boolean, default=False)
    skip_maven = db.Column(db.Boolean, default=False)
    skip_save = db.Column(db.Boolean, default=False)
    output_dir      = db.Column(db.String(500), default="docker/output")
    portainer_url            = db.Column(db.String(500), default="")
    portainer_username       = db.Column(db.String(200), default="")
    portainer_password       = db.Column(db.String(200), default="")
    portainer_environment_id = db.Column(db.String(10), default="")
    container_name           = db.Column(db.String(200), default="")
    created_at      = db.Column(db.DateTime, default=datetime.now)
    updated_at      = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class Build(db.Model):
    __tablename__  = "builds"
    id             = db.Column(db.Integer, primary_key=True)
    project_id     = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    project_name   = db.Column(db.String(200), default="")
    status         = db.Column(db.String(20), default="pending")
    image_tag      = db.Column(db.String(300), default="")
    output_file    = db.Column(db.String(500), default="")
    log_output     = db.Column(db.Text, default="")
    started_at     = db.Column(db.DateTime, default=datetime.now)
    finished_at    = db.Column(db.DateTime, nullable=True)
    duration_seconds = db.Column(db.Integer, nullable=True)
    upload_status  = db.Column(db.String(20), default="none")
    is_chain       = db.Column(db.Boolean, default=False)


def _migrate_db():
    new_cols = {
        "projects": [
            ("portainer_url", "TEXT DEFAULT ''"),
            ("portainer_username", "TEXT DEFAULT ''"),
            ("portainer_password", "TEXT DEFAULT ''"),
            ("portainer_environment_id", "TEXT DEFAULT ''"),
            ("container_name", "TEXT DEFAULT ''"),
        ],
        "builds": [
            ("upload_status", "TEXT DEFAULT 'none'"),
            ("is_chain", "INTEGER DEFAULT 0"),
        ],
    }
    for table, cols in new_cols.items():
        existing = [row[1] for row in db.session.execute(db.text(f"PRAGMA table_info({table})")).fetchall()]
        for col_name, col_def in cols:
            if col_name not in existing:
                db.session.execute(db.text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
    db.session.commit()


with app.app_context():
    db.create_all()
    _migrate_db()


def push_log(build_id, msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with _live_logs_lock:
        if build_id not in _live_logs:
            _live_logs[build_id] = []
        _live_logs[build_id].append(f"[{ts}] {msg}")


def _is_cancelled(build_id):
    with _build_cancelled_lock:
        return build_id in _build_cancelled


def _kill_process_tree(proc):
    if not proc or not proc.pid:
        return
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, timeout=5)
    except Exception:
        pass


def _stream_cmd(cmd, cwd, env, build_id, timeout=600):
    push_log(build_id, f"执行: {' '.join(cmd)}")
    process = subprocess.Popen(
        cmd, cwd=cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1
    )
    with _running_processes_lock:
        _running_processes[build_id] = process
    try:
        for line in iter(process.stdout.readline, ""):
            line = line.rstrip("\n\r")
            if line:
                push_log(build_id, line)
        process.wait(timeout=timeout)
        return process.returncode
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        process.wait()
        raise
    finally:
        with _running_processes_lock:
            if _running_processes.get(build_id) is process:
                del _running_processes[build_id]


def run_build(project_id, build_id):
    status = "failed"
    image_tag = ""
    output_file = ""
    try:
        with app.app_context():
            proj = db.session.get(Project, project_id)
            if not proj:
                return
            cfg = {k: getattr(proj, k) for k in (
                "id", "name", "project_root", "maven_command", "maven_settings",
                "maven_java_home", "maven_profile", "maven_offline", "dockerfile_dir",
                "dockerfile_name", "image_tag_prefix", "image_tag_suffix", "compress",
                "no_cache", "skip_maven", "skip_save", "output_dir"
            )}
            cfg["project_root"] = os.path.abspath(cfg["project_root"])
            b = db.session.get(Build, build_id)
            if b:
                b.status = "running"
                db.session.commit()

        with _live_logs_lock:
            _live_logs[build_id] = []

        env = os.environ.copy()
        if cfg["maven_java_home"]:
            env["JAVA_HOME"] = cfg["maven_java_home"]

        ts = datetime.now().strftime("%m%d-%H%M")
        tag_suffix = cfg["image_tag_suffix"]
        image_tag = f"{cfg['image_tag_prefix']}{tag_suffix}{ts}" if tag_suffix else f"{cfg['image_tag_prefix']}{ts}"
        output_base = f"build_{ts}"

        dockerfile_dir = os.path.join(cfg["project_root"], cfg["dockerfile_dir"]) if cfg["dockerfile_dir"] else cfg["project_root"]
        output_dir_abs = os.path.join(cfg["project_root"], cfg["output_dir"]) if cfg["output_dir"] else os.path.join(dockerfile_dir, "output")
        dockerfile_path = os.path.join(dockerfile_dir, cfg["dockerfile_name"])
        os.makedirs(output_dir_abs, exist_ok=True)

        suffix = "tar.gz" if cfg["compress"] == "Gzip" else "tar"
        output_file = os.path.join(output_dir_abs, f"{output_base}.{suffix}")

        status = "success"

        # Step 0: Maven
        if not cfg["skip_maven"] and cfg["maven_command"] and not _is_cancelled(build_id):
            push_log(build_id, "========== Step 0: Maven 打包 ==========")
            mvn_args = ["clean", "package", "-Dmaven.test.skip=true"]
            if cfg["maven_profile"]:
                mvn_args += ["-P", cfg["maven_profile"]]
            if cfg["maven_settings"]:
                mvn_args += ["-s", cfg["maven_settings"]]
            if cfg["maven_offline"]:
                mvn_args += ["-o"]
            mvn_cmd = cfg["maven_command"]
            push_log(build_id, f"命令: {mvn_cmd} {' '.join(mvn_args)}")
            if cfg["maven_java_home"]:
                push_log(build_id, f"JAVA_HOME: {cfg['maven_java_home']}")
            if not os.path.isfile(mvn_cmd):
                raise RuntimeError(f"Maven 命令不存在: {mvn_cmd}")
            rc = _stream_cmd([mvn_cmd] + mvn_args, cwd=cfg["project_root"], env=env, build_id=build_id)
            if rc != 0 and not _is_cancelled(build_id):
                raise RuntimeError(f"Maven 打包失败 (exit code {rc})")
            if _is_cancelled(build_id):
                raise RuntimeError("build cancelled")
            push_log(build_id, "Maven 打包成功")

        if _is_cancelled(build_id):
            raise RuntimeError("build cancelled")

        jar_path = os.path.join(cfg["project_root"], "my-service", "target", "my-service.jar")
        if os.path.exists(jar_path):
            push_log(build_id, f"JAR: {jar_path} ({os.path.getsize(jar_path) / 1048576:.2f} MB)")
        else:
            push_log(build_id, "[WARN] 未找到 JAR 文件，继续执行 Docker")

        # Step 1: Docker
        if not _is_cancelled(build_id):
            push_log(build_id, "========== Step 1: Docker 构建 ==========")
            push_log(build_id, f"镜像: {image_tag}")
            push_log(build_id, f"Dockerfile: {dockerfile_path}")
            if not os.path.isfile(dockerfile_path):
                raise RuntimeError(f"Dockerfile 不存在: {dockerfile_path}")
            docker_args = ["build", "-t", image_tag, "-f", dockerfile_path]
            if cfg["no_cache"]:
                docker_args.append("--no-cache")
                push_log(build_id, "模式: 无缓存")
            else:
                push_log(build_id, "模式: 有缓存")
            docker_args.append(cfg["project_root"])
            if not which("docker"):
                raise RuntimeError("未找到 docker 命令")
            push_log(build_id, "正在执行 docker build ...")
            rc = _stream_cmd(["docker"] + docker_args, cwd=cfg["project_root"], env=env, build_id=build_id)
            if rc != 0 and not _is_cancelled(build_id):
                raise RuntimeError(f"Docker 构建失败 (exit code {rc})")
            if _is_cancelled(build_id):
                raise RuntimeError("build cancelled")
            push_log(build_id, "Docker 构建成功")

        if _is_cancelled(build_id):
            raise RuntimeError("build cancelled")

        # Step 2: Save
        if not cfg["skip_save"] and not _is_cancelled(build_id):
            push_log(build_id, "========== Step 2: 保存镜像 ==========")
            temp_tar = os.path.join(output_dir_abs, f"{output_base}.tmp.tar")
            push_log(build_id, f"docker save -o {temp_tar} {image_tag}")
            push_log(build_id, f"docker save -o {temp_tar} {image_tag}")
            rc = _stream_cmd(["docker", "save", "-o", temp_tar, image_tag], cwd=cfg["project_root"], env=env, build_id=build_id)
            if rc != 0 and not _is_cancelled(build_id):
                if os.path.exists(temp_tar):
                    os.remove(temp_tar)
                raise RuntimeError(f"docker save 失败 (exit code {rc})")
            push_log(build_id, "docker save 完成")
            if cfg["compress"] == "Gzip":
                push_log(build_id, "压缩为 tar.gz ...")
                with open(temp_tar, "rb") as fin, gzip.open(output_file, "wb") as fout:
                    fout.writelines(fin)
                os.remove(temp_tar)
            else:
                os.rename(temp_tar, output_file)
            push_log(build_id, f"保存完成: {output_file}")
            push_log(build_id, f"大小: {os.path.getsize(output_file) / 1048576:.2f} MB")
            push_log(build_id, f"加载命令: docker load < {os.path.basename(output_file)}")

        push_log(build_id, "========================================")
        push_log(build_id, "构建完成")

    except subprocess.TimeoutExpired:
        status = "timeout"
        push_log(build_id, "[ERROR] 构建超时")
    except RuntimeError as e:
        if str(e) == "build cancelled":
            status = "stopped"
            push_log(build_id, "构建已停止")
        else:
            status = "failed"
            push_log(build_id, f"[ERROR] {e}")
    except Exception as e:
        if _is_cancelled(build_id):
            status = "stopped"
            push_log(build_id, "构建已停止")
        else:
            status = "failed"
            push_log(build_id, f"[ERROR] {e}")
    finally:
        if build_id:
            with _build_cancelled_lock:
                _build_cancelled.discard(build_id)
            with _live_logs_lock:
                lines = list(_live_logs.get(build_id, []))
            with app.app_context():
                b = db.session.get(Build, build_id)
                if b:
                    b.status = status
                    if image_tag:
                        b.image_tag = image_tag
                    if output_file and os.path.exists(output_file):
                        b.output_file = output_file
                    b.log_output = "\n".join(lines)
                    b.finished_at = datetime.now()
                    if b.started_at:
                        b.duration_seconds = int((b.finished_at - b.started_at).total_seconds())
                    db.session.commit()
            with _live_logs_lock:
                _live_logs.pop(build_id, None)


# ========== Routes ==========

@app.route("/")
def index():
    return redirect(url_for("projects"))


@app.route("/projects")
def projects():
    all_projects = Project.query.order_by(Project.updated_at.desc()).all()
    pids = [p.id for p in all_projects]
    builds_map = {}
    if pids:
        subq = db.session.query(Build.project_id, db.func.max(Build.id).label("max_id")).filter(
            Build.project_id.in_(pids)).group_by(Build.project_id).subquery()
        for b in db.session.query(Build).join(subq, Build.id == subq.c.max_id).all():
            builds_map[b.project_id] = b
    return render_template("projects.html", projects=all_projects, builds_by_project=builds_map)


@app.route("/projects/new", methods=["GET", "POST"])
def project_new():
    if request.method == "POST":
        p = Project(name=request.form["name"], description=request.form.get("description", ""))
        _fill_project(p, request.form)
        db.session.add(p)
        db.session.commit()
        flash(f"项目「{p.name}」创建成功", "success")
        return redirect(url_for("projects"))
    return render_template("project_form.html", project=None)


@app.route("/projects/<int:id>/edit", methods=["GET", "POST"])
def project_edit(id):
    p = db.session.get(Project, id)
    if not p:
        flash("项目不存在", "error")
        return redirect(url_for("projects"))
    if request.method == "POST":
        _fill_project(p, request.form)
        db.session.commit()
        flash(f"项目「{p.name}」已更新", "success")
        return redirect(url_for("projects"))
    return render_template("project_form.html", project=p)


@app.route("/projects/<int:id>/delete", methods=["POST"])
def project_delete(id):
    p = db.session.get(Project, id)
    if p:
        Build.query.filter_by(project_id=id).delete()
        db.session.delete(p)
        db.session.commit()
        flash(f"项目「{p.name}」已删除", "success")
    return redirect(url_for("projects"))


@app.route("/projects/<int:id>")
def project_detail(id):
    p = db.session.get(Project, id)
    if not p:
        flash("项目不存在", "error")
        return redirect(url_for("projects"))
    builds = Build.query.filter_by(project_id=id).order_by(desc(Build.started_at)).limit(50).all()
    return render_template("project_detail.html", project=p, builds=builds)


@app.route("/projects/<int:id>/build", methods=["POST"])
def trigger_build(id):
    p = db.session.get(Project, id)
    if not p:
        return jsonify({"error": "not found"}), 404
    running = Build.query.filter_by(project_id=id, status="running").first()
    if running:
        with _build_cancelled_lock:
            _build_cancelled.add(running.id)
        with _running_processes_lock:
            proc = _running_processes.get(running.id)
        if proc:
            _kill_process_tree(proc)
        running.status = "stopped"
        running.finished_at = datetime.now()
        if running.started_at:
            running.duration_seconds = int((running.finished_at - running.started_at).total_seconds())
        db.session.add(running)
        db.session.commit()
    build = Build(project_id=id, project_name=p.name, status="pending", started_at=datetime.now())
    db.session.add(build)
    db.session.commit()
    bid = build.id
    threading.Thread(target=run_build, args=(id, bid), daemon=True).start()
    return redirect(url_for("build_detail", id=bid))


@app.route("/builds")
def builds():
    page = request.args.get("page", 1, type=int)
    pagination = Build.query.order_by(desc(Build.started_at)).paginate(page=page, per_page=50)
    return render_template("builds.html", builds=pagination.items, pagination=pagination)


@app.route("/builds/<int:id>")
def build_detail(id):
    b = db.session.get(Build, id)
    if not b:
        flash("构建记录不存在", "error")
        return redirect(url_for("builds"))
    return render_template("build_detail.html", build=b)


@app.route("/builds/<int:id>/delete", methods=["POST"])
def build_delete(id):
    b = db.session.get(Build, id)
    if b:
        pid = b.project_id
        db.session.delete(b)
        db.session.commit()
        flash(f"构建记录 #{id} 已删除", "success")
        return redirect(url_for("project_detail", id=pid))
    flash("构建记录不存在", "error")
    return redirect(url_for("builds"))


_portainer_jwt_cache = {}
_portainer_jwt_cache_lock = threading.Lock()


def _portainer_auth(url, username, password):
    if not url or not username or not password:
        log.warning("Portainer auth skipped: missing url/username/password")
        return None, "未配置 Portainer 地址、用户名或密码"
    cache_key = f"{url}:{username}"
    with _portainer_jwt_cache_lock:
        entry = _portainer_jwt_cache.get(cache_key)
        if entry and entry["expires"] > time.time():
            log.debug(f"Portainer JWT cache hit for {cache_key}")
            return entry["jwt"], None
    auth_url = url.rstrip("/") + "/api/auth"
    log.info(f"Portainer auth: POST {auth_url}")
    try:
        r = requests.post(auth_url, json={"username": username, "password": password}, timeout=10)
        log.info(f"Portainer auth response: HTTP {r.status_code}")
        if r.status_code == 200:
            jwt = r.json().get("jwt")
            if jwt:
                with _portainer_jwt_cache_lock:
                    _portainer_jwt_cache[cache_key] = {"jwt": jwt, "expires": time.time() + 28800}
                log.info("Portainer auth success, JWT obtained")
                return jwt, None
            else:
                msg = "Portainer 认证响应中未找到 JWT"
                log.error(msg)
                return None, msg
        else:
            msg = f"Portainer 认证失败 (HTTP {r.status_code}): {r.text[:300]}"
            log.error(msg)
            return None, msg
    except requests.exceptions.ConnectionError as e:
        msg = f"无法连接 Portainer ({url}): {e}"
        log.error(msg)
        return None, msg
    except requests.exceptions.Timeout:
        msg = f"Portainer 连接超时 ({url})"
        log.error(msg)
        return None, msg
    except Exception as e:
        msg = f"Portainer 认证异常: {e}"
        log.error(msg)
        return None, msg


def _push_upload_log(build_id, msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [Portainer] {msg}"
    with _live_logs_lock:
        if build_id not in _live_logs:
            _live_logs[build_id] = []
        _live_logs[build_id].append(line)


def _flush_upload_log(build_id):
    with _live_logs_lock:
        lines = _live_logs.get(build_id, [])
    if not lines:
        return
    with app.app_context():
        b = db.session.get(Build, build_id)
        if b:
            existing = (b.log_output or "").rstrip()
            new_log = "\n".join(lines)
            b.log_output = (existing + "\n" + new_log).strip() if existing else new_log
            db.session.commit()


def _get_container_create_body(inspect_data, new_image):
    body = {}
    config = inspect_data.get("Config", {})
    host_config = inspect_data.get("HostConfig", {})

    create_fields = [
        "Hostname", "Domainname", "User", "AttachStdin", "AttachStdout",
        "AttachStderr", "ExposedPorts", "Tty", "OpenStdin", "StdinOnce",
        "Env", "Cmd", "Entrypoint", "Volumes", "WorkingDir", "Labels",
        "StopSignal", "StopTimeout", "Healthcheck",
    ]
    for key in create_fields:
        if key in config:
            body[key] = config[key]

    body["Image"] = new_image

    host_copy_keys = [
        "AutoRemove", "Binds", "BlkioDeviceReadBps", "BlkioDeviceReadIOps",
        "BlkioDeviceWriteBps", "BlkioDeviceWriteIOps", "BlkioWeight",
        "BlkioWeightDevice", "CapAdd", "CapDrop", "Cgroup", "CgroupParent",
        "CgroupnsMode", "ConsoleSize", "ContainerIDFile", "CpuCount",
        "CpuPercent", "CpuPeriod", "CpuQuota", "CpuRealtimePeriod",
        "CpuRealtimeRuntime", "CpuShares", "CpusetCpus", "CpusetMems",
        "DeviceCgroupRules", "DeviceRequests", "Devices", "Dns", "DnsOptions",
        "DnsSearch", "ExtraHosts", "GroupAdd", "IOMaximumBandwidth",
        "IOMaximumIOps", "Init", "IpcMode", "Isolation", "Links",
        "LogConfig", "MaskedPaths", "Memory", "MemoryReservation",
        "MemorySwap", "MemorySwappiness", "NanoCpus", "NetworkMode",
        "OomKillDisable", "OomScoreAdj", "PidMode", "PidsLimit",
        "PortBindings", "Privileged", "PublishAllPorts", "ReadonlyPaths",
        "ReadonlyRootfs", "RestartPolicy", "Runtime", "SecurityOpt",
        "ShmSize", "Sysctls", "UTSMode", "Ulimits", "UsernsMode",
        "VolumeDriver", "VolumesFrom", "Mounts",
    ]
    body["HostConfig"] = {k: host_config[k] for k in host_copy_keys if k in host_config}

    networks = inspect_data.get("NetworkSettings", {}).get("Networks", {})
    if networks:
        body["NetworkingConfig"] = {"EndpointsConfig": {}}
        for net_name, net_cfg in networks.items():
            body["NetworkingConfig"]["EndpointsConfig"][net_name] = {
                "IPAMConfig": net_cfg.get("IPAMConfig", {}),
            }

    return body


def _portainer_upload(build_id):
    log.info(f"[Build #{build_id}] Upload thread started")
    try:
        with app.app_context():
            b = db.session.get(Build, build_id)
            if not b:
                log.error(f"[Build #{build_id}] Build not found")
                return
            proj = db.session.get(Project, b.project_id)
            if not proj:
                log.error(f"[Build #{build_id}] Project not found")
                return
            b.upload_status = "uploading"
            db.session.commit()
            output_file = b.output_file
            image_tag = b.image_tag
            portainer_url = proj.portainer_url
            portainer_username = proj.portainer_username
            portainer_password = proj.portainer_password
            portainer_environment_id = proj.portainer_environment_id

        _push_upload_log(build_id, "开始上传镜像到 Portainer ...")

        if not output_file or not os.path.exists(output_file):
            raise RuntimeError(f"镜像文件不存在: {output_file}")

        file_size_mb = os.path.getsize(output_file) / 1048576
        log.info(f"[Build #{build_id}] File: {output_file} ({file_size_mb:.2f} MB)")

        jwt, auth_err = _portainer_auth(portainer_url, portainer_username, portainer_password)
        if auth_err:
            raise RuntimeError(auth_err)

        env_id = portainer_environment_id.strip()
        if not env_id:
            raise RuntimeError("未配置 Portainer Environment ID")

        url = portainer_url.rstrip("/") + f"/api/endpoints/{env_id}/docker/images/load"
        _push_upload_log(build_id, f"上传文件: {output_file} ({file_size_mb:.2f} MB)")
        _push_upload_log(build_id, f"目标: {url}")
        log.info(f"[Build #{build_id}] POST {url}")

        content_type = "application/x-tar"
        if output_file.endswith(".gz"):
            content_type = "application/gzip"

        with open(output_file, "rb") as fh:
            r = requests.post(url, data=fh, headers={
                "Authorization": f"Bearer {jwt}",
                "Content-Type": content_type,
            }, timeout=600)

        log.info(f"[Build #{build_id}] Upload response: HTTP {r.status_code}")
        if r.text:
            log.info(f"[Build #{build_id}] Response body: {r.text[:500]}")

        if r.status_code in (200, 201, 204):
            _push_upload_log(build_id, "镜像上传成功")
            log.info(f"[Build #{build_id}] Upload success")
            with app.app_context():
                b = db.session.get(Build, build_id)
                if b:
                    b.upload_status = "upload_success"
                    db.session.commit()
            _flush_upload_log(build_id)
        else:
            raise RuntimeError(f"Portainer 返回错误 (HTTP {r.status_code}): {r.text[:500]}")

    except Exception as e:
        log.error(f"[Build #{build_id}] Upload failed: {e}")
        _push_upload_log(build_id, f"[ERROR] 上传失败: {e}")
        with app.app_context():
            b = db.session.get(Build, build_id)
            if b:
                b.upload_status = "upload_failed"
                db.session.commit()
        _flush_upload_log(build_id)


def _portainer_deploy(build_id):
    log.info(f"[Build #{build_id}] Deploy thread started")
    try:
        with app.app_context():
            b = db.session.get(Build, build_id)
            if not b:
                log.error(f"[Build #{build_id}] Build not found")
                return
            proj = db.session.get(Project, b.project_id)
            if not proj:
                log.error(f"[Build #{build_id}] Project not found")
                return
            b.upload_status = "deploying"
            db.session.commit()
            image_tag = b.image_tag
            portainer_url = proj.portainer_url
            portainer_username = proj.portainer_username
            portainer_password = proj.portainer_password
            portainer_environment_id = proj.portainer_environment_id
            container_name = proj.container_name

        _push_upload_log(build_id, "开始部署容器 ...")

        jwt, auth_err = _portainer_auth(portainer_url, portainer_username, portainer_password)
        if auth_err:
            raise RuntimeError(auth_err)

        env_id = portainer_environment_id.strip()
        if not env_id:
            raise RuntimeError("未配置 Portainer Environment ID")

        container_name = container_name.strip()
        if not container_name:
            raise RuntimeError("未配置容器名称")

        if not image_tag:
            raise RuntimeError("镜像标签为空，无法部署")

        base_url = portainer_url.rstrip("/")
        headers = {"Authorization": f"Bearer {jwt}"}

        _push_upload_log(build_id, f"部署容器: {container_name} (镜像: {image_tag})")

        # Step 1: inspect existing container to get its full config
        inspect_url = f"{base_url}/api/endpoints/{env_id}/docker/containers/{container_name}/json"
        log.info(f"[Build #{build_id}] GET {inspect_url}")
        r_inspect = requests.get(inspect_url, headers=headers, timeout=30)

        container_id = None
        create_body = {"Image": image_tag}

        if r_inspect.status_code == 200:
            inspect_data = r_inspect.json()
            container_id = inspect_data.get("Id", "")

            # Rename existing container to {name}-old
            old_name = f"{container_name}-old-{build_id}"
            rename_url = f"{base_url}/api/endpoints/{env_id}/docker/containers/{container_id}/rename?name=/{old_name}"
            log.info(f"[Build #{build_id}] Rename: POST {rename_url}")
            r_rename = requests.post(rename_url, headers=headers, timeout=30)
            if r_rename.status_code in (200, 201, 204):
                _push_upload_log(build_id, f"旧容器已重命名为: {old_name}")
            else:
                _push_upload_log(build_id, f"重命名旧容器返回 (HTTP {r_rename.status_code})，尝试强制删除")
                requests.delete(
                    f"{base_url}/api/endpoints/{env_id}/docker/containers/{container_name}?force=true",
                    headers=headers, timeout=30
                )

            # Build create body from existing config
            create_body = _get_container_create_body(inspect_data, image_tag)
        elif r_inspect.status_code == 404:
            _push_upload_log(build_id, "无旧容器，直接创建新容器")
        else:
            _push_upload_log(build_id, f"获取容器配置异常 (HTTP {r_inspect.status_code})，尝试直接创建")

        # Step 2: create new container
        _push_upload_log(build_id, f"创建新容器: {container_name}")
        create_url = f"{base_url}/api/endpoints/{env_id}/docker/containers/create?name={container_name}"
        log.info(f"[Build #{build_id}] POST {create_url}")
        r_create = requests.post(create_url, json=create_body, headers=headers, timeout=30)
        log.info(f"[Build #{build_id}] Create response: HTTP {r_create.status_code}")

        if r_create.status_code not in (200, 201):
            raise RuntimeError(f"创建容器失败 (HTTP {r_create.status_code}): {r_create.text[:500]}")

        new_container_id = r_create.json().get("Id", "")
        _push_upload_log(build_id, f"新容器已创建: {new_container_id[:12]}")

        # Step 3: start new container
        start_url = f"{base_url}/api/endpoints/{env_id}/docker/containers/{new_container_id}/start"
        log.info(f"[Build #{build_id}] POST {start_url}")
        r_start = requests.post(start_url, headers=headers, timeout=30)
        log.info(f"[Build #{build_id}] Start response: HTTP {r_start.status_code}")

        if r_start.status_code not in (200, 201, 204):
            raise RuntimeError(f"启动容器失败 (HTTP {r_start.status_code}): {r_start.text[:500]}")

        # Step 4: delete old renamed container
        if container_id:
            delete_url = f"{base_url}/api/endpoints/{env_id}/docker/containers/{container_id}?v=1&force=true"
            log.info(f"[Build #{build_id}] DELETE old container: {delete_url}")
            r_delete = requests.delete(delete_url, headers=headers, timeout=30)
            if r_delete.status_code in (200, 201, 204):
                _push_upload_log(build_id, "旧容器已清理")
            else:
                _push_upload_log(build_id, f"清理旧容器返回 (HTTP {r_delete.status_code})，可手动删除")

        _push_upload_log(build_id, f"容器 {container_name} 部署成功")
        log.info(f"[Build #{build_id}] Deploy success")

        with app.app_context():
            b = db.session.get(Build, build_id)
            if b:
                b.upload_status = "deploy_success"
                db.session.commit()
        _flush_upload_log(build_id)

    except Exception as e:
        log.error(f"[Build #{build_id}] Deploy failed: {e}")
        _push_upload_log(build_id, f"[ERROR] 部署失败: {e}")
        with app.app_context():
            b = db.session.get(Build, build_id)
            if b:
                b.upload_status = "deploy_failed"
                db.session.commit()
        _flush_upload_log(build_id)


@app.route("/api/builds/<int:id>/upload", methods=["POST"])
def api_upload(id):
    b = db.session.get(Build, id)
    if not b:
        return jsonify({"error": "not found"}), 404
    if not b.output_file or not os.path.exists(b.output_file):
        return jsonify({"error": "镜像文件不存在"}), 400
    if b.upload_status in ("uploading", "deploying"):
        return jsonify({"error": "已有操作进行中"}), 409
    project = db.session.get(Project, b.project_id)
    if not project or not project.portainer_url:
        return jsonify({"error": "未配置 Portainer，请先在项目详情中配置"}), 400
    _push_upload_log(id, "上传任务已提交")
    threading.Thread(target=_portainer_upload, args=(id,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/builds/<int:id>/deploy", methods=["POST"])
def api_deploy(id):
    b = db.session.get(Build, id)
    if not b:
        return jsonify({"error": "not found"}), 404
    if b.upload_status in ("uploading", "deploying"):
        return jsonify({"error": "已有操作进行中"}), 409
    if b.upload_status not in ("upload_success", "deploy_success", "deploy_failed"):
        return jsonify({"error": "请先上传镜像"}), 400
    project = db.session.get(Project, b.project_id)
    if not project or not project.portainer_url:
        return jsonify({"error": "未配置 Portainer"}), 400
    if not project.container_name:
        return jsonify({"error": "未配置容器名称"}), 400
    _push_upload_log(id, "部署任务已提交")
    threading.Thread(target=_portainer_deploy, args=(id,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/builds/<int:id>/delete-image", methods=["POST"])
def api_delete_image(id):
    b = db.session.get(Build, id)
    if not b:
        return jsonify({"error": "not found"}), 404
    if not b.image_tag:
        return jsonify({"error": "镜像标签为空"}), 400
    image_tag = b.image_tag

    def _do_delete_local_image(img_tag, bid):
        try:
            log.info(f"Deleting local Docker image: {img_tag}")
            push_log(bid, f"删除本地 Docker 镜像: {img_tag}")
            r = subprocess.run(
                ["docker", "rmi", img_tag],
                capture_output=True, text=True, timeout=60
            )
            if r.returncode == 0:
                log.info(f"Image {img_tag} deleted from local Docker")
                push_log(bid, f"本地镜像 {img_tag} 已删除")
            elif "not found" in r.stderr.lower() or "no such image" in r.stderr.lower():
                log.info(f"Image {img_tag} not found locally (already deleted)")
                push_log(bid, f"本地镜像不存在（可能已删除）")
            else:
                log.warning(f"docker rmi returned exit code {r.returncode}: {r.stderr[:200]}")
                push_log(bid, f"删除本地镜像失败 (code {r.returncode})")
        except Exception as e:
            log.error(f"Failed to delete local image: {e}")
            push_log(bid, f"删除本地镜像异常: {e}")
        _flush_upload_log(bid)

    threading.Thread(target=_do_delete_local_image, args=(image_tag, id), daemon=True).start()
    return jsonify({"ok": True})


def _build_upload_deploy_chain(project_id, build_id):
    log.info(f"[Build #{build_id}] Chain: build->upload->deploy started")
    try:
        while True:
            with app.app_context():
                b = db.session.get(Build, build_id)
                if not b:
                    return
                if b.status not in ("pending", "running"):
                    break
            time.sleep(3)

        with app.app_context():
            b = db.session.get(Build, build_id)
            if not b:
                return
            if b.status != "success":
                log.warning(f"[Build #{build_id}] Chain: build status is {b.status}, aborting upload/deploy")
                return

        log.info(f"[Build #{build_id}] Chain: build success, starting upload")
        _portainer_upload(build_id)

        while True:
            with app.app_context():
                b = db.session.get(Build, build_id)
                if not b:
                    return
                if b.upload_status not in ("uploading",):
                    break
            time.sleep(2)

        with app.app_context():
            b = db.session.get(Build, build_id)
            if not b:
                return
            if b.upload_status != "upload_success":
                log.warning(f"[Build #{build_id}] Chain: upload status is {b.upload_status}, aborting deploy")
                return

        log.info(f"[Build #{build_id}] Chain: upload success, starting deploy")
        _portainer_deploy(build_id)

        log.info(f"[Build #{build_id}] Chain: build->upload->deploy complete")
    except Exception as e:
        log.error(f"[Build #{build_id}] Chain failed: {e}")


@app.route("/projects/<int:id>/build-upload-deploy", methods=["POST"])
def trigger_build_upload_deploy(id):
    p = db.session.get(Project, id)
    if not p:
        return jsonify({"error": "not found"}), 404
    if not p.portainer_url:
        flash("请先配置 Portainer 信息后再使用一键构建部署", "error")
        return redirect(url_for("project_detail", id=id))
    running = Build.query.filter_by(project_id=id, status="running").first()
    if running:
        with _build_cancelled_lock:
            _build_cancelled.add(running.id)
        with _running_processes_lock:
            proc = _running_processes.get(running.id)
        if proc:
            _kill_process_tree(proc)
        running.status = "stopped"
        running.finished_at = datetime.now()
        if running.started_at:
            running.duration_seconds = int((running.finished_at - running.started_at).total_seconds())
        db.session.add(running)
        db.session.commit()
    build = Build(project_id=id, project_name=p.name, status="pending",
                  started_at=datetime.now(), is_chain=True)
    db.session.add(build)
    db.session.commit()
    bid = build.id
    threading.Thread(target=run_build, args=(id, bid), daemon=True).start()
    threading.Thread(target=_build_upload_deploy_chain, args=(id, bid), daemon=True).start()
    return redirect(url_for("build_detail", id=bid))


@app.route("/api/builds/<int:id>/upload-status")
def api_upload_status(id):
    b = db.session.get(Build, id)
    if not b:
        return jsonify({"error": "not found"}), 404
    with _live_logs_lock:
        lines = list(_live_logs.get(id, []))
    return jsonify({
        "upload_status": b.upload_status,
        "logs": lines,
    })


# ========== API ==========

@app.route("/api/builds/<int:id>/logs")
def build_logs(id):
    since = request.args.get("since", 0, type=int)
    b = db.session.get(Build, id)
    if not b:
        return jsonify({"error": "not found"}), 404
    if b.status != "running" and b.log_output:
        with _live_logs_lock:
            live = list(_live_logs.get(id, []))
        lines = b.log_output.split("\n")
        if live and b.upload_status in ("uploading", "deploying"):
            total = len(lines) + len(live)
            combined = lines + live
            return jsonify({
                "lines": combined[since:],
                "total": total,
                "status": b.status,
                "duration": b.duration_seconds,
            })
        return jsonify({
            "lines": lines[since:],
            "total": len(lines),
            "status": b.status,
            "duration": b.duration_seconds,
        })
    with _live_logs_lock:
        lines = list(_live_logs.get(id, []))
    return jsonify({
        "lines": lines[since:],
        "total": len(lines),
        "status": b.status,
        "duration": b.duration_seconds,
    })


@app.route("/api/builds/<int:id>/status")
def build_status(id):
    b = db.session.get(Build, id)
    if not b:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "id": b.id, "status": b.status, "duration": b.duration_seconds,
        "finished": b.finished_at.isoformat() if b.finished_at else None,
        "image_tag": b.image_tag,
        "output_file": b.output_file,
        "upload_status": b.upload_status,
    })


@app.route("/api/builds/<int:id>/stop", methods=["POST"])
def stop_build(id):
    with _build_cancelled_lock:
        _build_cancelled.add(id)
    with _running_processes_lock:
        p = _running_processes.get(id)
    if p:
        _kill_process_tree(p)
        push_log(id, "构建正在停止...")
        return jsonify({"ok": True})
    with app.app_context():
        b = db.session.get(Build, id)
        if b and b.status == "running":
            b.status = "stopped"
            b.finished_at = datetime.now()
            if b.started_at:
                b.duration_seconds = int((b.finished_at - b.started_at).total_seconds())
            db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/projects/<int:id>/running")
def project_running(id):
    b = Build.query.filter_by(project_id=id, status="running").first()
    if b:
        return jsonify({"running": True, "build_id": b.id})
    return jsonify({"running": False})


# ========== Helpers ==========

def _fill_project(p, form):
    p.name = form.get("name", p.name)
    p.description = form.get("description", "")
    for field in ["project_root", "maven_command", "maven_settings", "maven_java_home",
                  "maven_profile", "dockerfile_dir", "dockerfile_name", "image_tag_prefix",
                  "image_tag_suffix", "output_dir",
                  "portainer_url", "portainer_username", "portainer_password",
                  "portainer_environment_id", "container_name"]:
        setattr(p, field, form.get(field, ""))
    for bool_field in ["maven_offline", "no_cache", "skip_maven", "skip_save"]:
        setattr(p, bool_field, bool(form.get(bool_field)))
    p.compress = form.get("compress", "Gzip")


if __name__ == "__main__":
    print("=" * 52)
    print("  Build Manager - Docker 构建管理工具")
    print("=" * 52)
    print(f"  数据库: {os.path.join(BASE_DIR, 'build_manager.db')}")
    print(f"  启动地址: http://127.0.0.1:5000")
    print("=" * 52)
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="127.0.0.1", port=5000, debug=debug_mode, use_reloader=False)
