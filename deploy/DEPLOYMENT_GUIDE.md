# dbrescue Production Deployment Guide

This guide covers deploying the **dbrescue** disaster recovery, integrity auditing, and observability platform into production environments.

---

## 1. Active Local Deployment Status

The application is deployed and actively serving traffic locally:
- **Streamlit Control Plane:** [http://127.0.0.1:8501](http://127.0.0.1:8501)
- **Status:** `HTTP 200 OK`
- **Environment:** Configured with `.env`

---

## 2. AWS Production Deployment (EC2 / CloudFormation)

The fastest, production-ready way to deploy `dbrescue` on AWS with full Docker engine socket access for disaster recovery drills.

### Option A: Using AWS CloudFormation (Single-Click)
The template [`deploy/aws-cloudformation.yaml`](aws-cloudformation.yaml) automates the entire provisioning:
- Provisions a hardened **Amazon Linux 2023** instance (default `t3.medium`).
- Attaches an encrypted 40GB EBS `gp3` storage volume.
- Creates least-privilege IAM roles with AWS Systems Manager (SSM) integration (no open SSH ports needed).
- Applies a Security Group binding MySQL internal listeners strictly to `127.0.0.1` and exposing port `8501` for the Streamlit UI.
- Auto-runs UserData script to clone the repository, generate random production secrets, boot MySQL 8.4 LTS via Docker Compose, and seed relational fixtures.

#### Deploy via AWS CLI:
```bash
aws cloudformation create-stack \
  --stack-name dbrescue-production \
  --template-body file://deploy/aws-cloudformation.yaml \
  --parameters \
      ParameterKey=VpcId,ParameterValue=vpc-xxxxxxxxx \
      ParameterKey=SubnetId,ParameterValue=subnet-xxxxxxxxx \
      ParameterKey=AdminIpCIDR,ParameterValue=YOUR_IP_ADDRESS/32 \
  --capabilities CAPABILITY_NAMED_IAM
```

Once the stack status reaches `CREATE_COMPLETE`, view the outputs:
```bash
aws cloudformation describe-stacks --stack-name dbrescue-production --query "Stacks[0].Outputs"
```

---

### Option B: Deploy on Existing Linux / EC2 Host
Run the automated installation script [`deploy/deploy-ec2.sh`](deploy-ec2.sh):
```bash
# Clone the repository
git clone https://github.com/Narendrapendli/dbrescue.git
cd dbrescue

# Make script executable and run
chmod +x deploy/deploy-ec2.sh
./deploy/deploy-ec2.sh
```

---

## 3. Deploying to Streamlit Community Cloud (Zero Infrastructure)

If you want a free, hosted public dashboard directly connected to GitHub:
1. Navigate to [share.streamlit.io](https://share.streamlit.io).
2. Sign in with GitHub.
3. Click **Create app** and configure:
   - **Repository:** `Narendrapendli/dbrescue`
   - **Branch:** `main`
   - **Main file path:** `app/main.py`
4. In **Advanced settings**, paste the contents of `.env` under **Secrets**.
5. Click **Deploy!**

---

## 4. Production Service Endpoints & Security Policy

| Service | Port | Binding | Security Policy |
| :--- | :---: | :---: | :--- |
| **Streamlit Web UI** | `8501` | Host Public / Restricted CIDR | Admin Control Plane & DR Console |
| **MySQL Database** | `3306` | Strictly `127.0.0.1` (Internal) | Never exposed to public internet |
| **Grafana Telemetry** | `3000` | Host Public / Restricted CIDR | Password authenticated (`admin`) |
| **Prometheus API** | `9090` | Host Public / Restricted CIDR | Time-series scraper |
