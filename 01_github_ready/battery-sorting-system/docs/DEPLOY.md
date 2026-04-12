**Server Setup**
1. Install Python 3.10+ and MySQL 8.0 on the server.
2. Enter the server directory, create a virtual environment and install dependencies.
Windows PowerShell:
```powershell
cd server
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
```
Linux or macOS:
```bash
cd server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Create the database and tables if this is a fresh install.

```sql
CREATE DATABASE IF NOT EXISTS battery_sys DEFAULT CHARSET utf8mb4;
USE battery_sys;

CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(64) NOT NULL UNIQUE,
  password VARCHAR(255) NOT NULL,
  role VARCHAR(20) DEFAULT 'operator'
);

CREATE TABLE IF NOT EXISTS detection_records (
  id INT AUTO_INCREMENT PRIMARY KEY,
  detection_time DATETIME DEFAULT CURRENT_TIMESTAMP,
  operator VARCHAR(64) DEFAULT '',
  result VARCHAR(128) DEFAULT '',
  image_path VARCHAR(255) DEFAULT '',
  confidence FLOAT DEFAULT 0.95,
  is_reviewed TINYINT DEFAULT 0,
  INDEX idx_detection_time (detection_time)
);
```

4. Configure server settings using either environment variables or `server_config.json`.
Use `server_config.example.json` as the key reference. Environment variables take priority if both are set.
Important keys include `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `UPLOAD_FOLDER`, and `APP_SECRET_KEY`.

5. Start the server.

```bash
python server.py
```

6. If you run behind a reverse proxy and want secure cookies, set `SESSION_COOKIE_SECURE=1` and ensure TLS termination is enabled.

**Client Setup**
1. Install Python 3.10+ on the edge device.
2. Enter the client directory and install dependencies.
Windows PowerShell:
```powershell
cd client
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements_client.txt
```
Linux or macOS:
```bash
cd client
python -m venv .venv
source .venv/bin/activate
pip install -r requirements_client.txt
```

3. Edit `config.json` to point `server_ip` and `server_port` at your server, and adjust camera and model settings.
4. Start the client UI.

```bash
python final.py
```

**Notes**
1. The server will auto-apply schema upgrades on startup in `upgrade_db_structure()`.
2. Logs are written to `logs/server.log` and `logs/client.log`.
