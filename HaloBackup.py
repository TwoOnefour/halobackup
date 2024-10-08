import os.path
import sys
import yaml
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
import base64
import requests
import time
from urllib3 import disable_warnings
import random
import datetime
import pytz

disable_warnings()


def random_uuid():
    """
    用于获取访问接口所需的 `_csrf`
    """
    def replace_char(c):
        r = random.randint(0, 15)
        if c == 'x':
            return hex(r)[2:]  # Get the hex representation and remove '0x'
        elif c == 'y':
            return hex((r & 0x3) | 0x8)[2:]  # Ensure first bit is 1

    uuid_format = "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx"
    return ''.join(replace_char(c) if c in 'xy' else c for c in uuid_format)


class HaloBackup:
    def __init__(self):
        """
        在这里初始化远程和本地url以及参数
        """
        self.RemoteSession = None
        self.localSession = None
        self.remote_url = None  # 远程url，即被同步的服务器
        self.local_url = None  # 本地url，即要同步的服务器
        # self.session.verify = False
        self.username = None  # 在这里修改账号密码
        self.password = None
        self.backupjson = None
        self.random_uuid = random_uuid()

    def loadProfile(self):
        """
        加载本地配置
        """
        with open('settings.yml', 'r') as f:
            yml = yaml.load(f, Loader=yaml.FullLoader)["settings"]
            self.username = yml['username']
            self.password = yml['password']
            self.local_url = yml["local_url"]
            self.remote_url = yml["remote_url"]
            if self.password == "xxx":
                print("请修改文件夹下的settings.yml再使用")
                sys.exit(1)

    def downloadBackup(self):
        """
        下载备份
        """

        while not self.getBackUpStatus():
            time.sleep(1)
            # print("备份还没准备好")
        if not os.path.exists("backup"):
            os.makedirs("backup")
        if os.path.exists(f'backup/{self.backupjson["items"][0]["status"]["filename"]}'):
            print("文件存在，跳过下载")
            return
        # 到这里就是决定要下载了，把其他的全删了
        for item in os.listdir('backup'):
            if ".zip" in item:
                os.remove("backup/" + item)
        with open(f'backup/{self.backupjson["items"][0]["status"]["filename"]}', "wb") as f:
            with self.RemoteSession.get(
                    f'{self.remote_url}/apis/api.console.migration.halo.run/v1alpha1/backups/{self.backupjson["items"][0]["metadata"]["name"]}/files/{self.backupjson["items"][0]["status"]["filename"]}',
                    stream=True) as r:
                length = int(r.headers["Content-Length"])
                print(
                    f'文件名{self.backupjson["items"][0]["status"]["filename"]},文件大小为:{length / 1024 / 1024:.2f} MB')
                now = 0
                for index, line in enumerate(r.iter_content(chunk_size=512)):
                    f.write(line)
                    now += len(line)
                    sys.stdout.write(f"\r当前下载备份文件进度 {now / length * 100:.2f} %")
                    sys.stdout.flush()

    def backup(self):
        """
        请求网站生成备份文件
        """

        # 获取当前时间
        now = datetime.datetime.now(pytz.timezone('Asia/Shanghai')) + datetime.timedelta(hours=1)

        # 格式化时间为 ISO 8601 格式
        iso_format = now.isoformat()
        self.RemoteSession.post(f"{self.remote_url}/apis/migration.halo.run/v1alpha1/backups", json={
            "apiVersion": "migration.halo.run/v1alpha1",
            "kind": "Backup",
            "metadata": {
                "generateName": "backup-",
                "name": ""
            },
            "spec": {
                "expiresAt": iso_format[:-6] + "z"
            }

        })

    def encrypt_password(self, password: str, public_key) -> str:
        """
        加密密码
        """
        cipher = PKCS1_v1_5.new(public_key)
        encrypted_password = cipher.encrypt(password.encode('utf-8'))
        return base64.b64encode(encrypted_password).decode('utf-8')

    def upload(self):
        """
        上传备份到本地网站
        """
        if self.backupjson is None:
            self.RemoteSession = self.login(self.remote_url, self.username, self.password)
            self.getBackUpStatus()

        with open(f'backup/{self.backupjson["items"][0]["status"]["filename"]}', 'rb') as f:
            # 上传数据
            data = {
                'relativePath': None,
                "name": self.backupjson["items"][0]["status"]["filename"],
                "type": 'application/x-zip-compressed',
                'file': (self.backupjson["items"][0]["status"]["filename"], f, 'application/x-zip-compressed')
                # 文件名、文件内容、文件类型
            }

            # 创建 MultipartEncoder 对象
            # multipart_encoder = MultipartEncoder(fields=data)

            # 设置请求头
            # headers = {'Content-Type': multipart_encoder.content_type}

            # 发送 POST 请求
            response = self.localSession.post(
                f'{self.local_url}/apis/api.console.migration.halo.run/v1alpha1/restorations',
                files=data)

        # 打印响应状态码
        # print(response.status_code)

        # 打印响应内容
        print(response.text)

    def restart(self):
        """
        请求本地网站重启halo
        """
        self.localSession.headers.update({"X-XSRF-TOKEN": self.random_uuid})
        self.localSession.post(f'{self.local_url}/actuator/restart')

    def login(self, url):
        """
        登陆网站
        :param url: halo的url
        :return: session登陆后的实例
        """
        session = requests.session()
        session.verify = False
        session.cookies.update({"XSRF-TOKEN": self.random_uuid})
        session.get(f"{url}/console")
        public_key_base64 = session.get(f'{url}/login/public-key').json()["base64Format"]
        # 解码 Base64 格式的公钥
        # 解析公钥
        public_key = RSA.import_key(base64.b64decode(public_key_base64))

        # 加密逻辑
        # 要加密的密码

        encrypted_password = self.encrypt_password(self.password, public_key)

        # print(encrypted_password)
        res = session.post(f"{url}/login?remember-me=false",
                           data={"_csrf": self.random_uuid, "username": self.username,
                                 "password": encrypted_password})
        return session

    def getBackUpStatus(self):
        """
        获取备份状态
        :return: 如果有备份，则返回成功，否则返回失败
        """
        self.backupjson = self.RemoteSession.get(
            f"{self.remote_url}/apis/migration.halo.run/v1alpha1/backups?sort=metadata.creationTimestamp,desc").json()

        for i in range(len(self.backupjson["items"])):  # 找到第一个备份成功的下载
            if self.backupjson["items"][i]["status"]["phase"] == "SUCCEEDED":
                return True
        if len(self.backupjson["items"]) < 1:
            self.backup()
        return False

    def run(self):
        """
        程序主方法
        """
        self.loadProfile()
        self.RemoteSession = self.login(self.remote_url)
        self.getBackUpStatus()
        self.downloadBackup()
        self.localSession = self.login(self.local_url)
        self.upload()
        self.restart()


if __name__ == "__main__":
    HaloBackup().run()

