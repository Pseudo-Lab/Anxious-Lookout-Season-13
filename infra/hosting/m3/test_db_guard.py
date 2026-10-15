"""URL query overrides must fail before any engine/connection; role is observed."""
from urllib.parse import urlencode
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
import verify_database as checker

PASSWORD = "synthetic-api-password"
ADMIN = "postgresql+psycopg://postgres:synthetic-admin-password@postgres/hosting"
API = "postgresql+psycopg://anxious_api:" + PASSWORD + "@postgres/hosting"


@pytest.fixture
def inputs(monkeypatch):
    values={"ADMIN_DATABASE_URL":ADMIN,"DATABASE_URL":API,"API_DATABASE_PASSWORD":PASSWORD}
    monkeypatch.setattr(checker,"secret",values.__getitem__)
    return values


@pytest.mark.parametrize("key,value", [("host","other-db"),("user","postgres"),("password","other-password"),
    ("dbname","other-database"),("database","other-database"),("hostaddr","127.0.0.2"),("service","other-service"),
    ("servicefile","/other/service"),("options","-cstatement_timeout=0"),("port","5433"),("sslmode","disable")])
@pytest.mark.parametrize("field",["ADMIN_DATABASE_URL","DATABASE_URL"])
def test_query_refused_before_engine_creation(inputs,monkeypatch,field,key,value):
    calls=[]
    monkeypatch.setattr(checker,"create_engine",lambda *a,**k:calls.append((a,k)))
    inputs[field]+="?"+urlencode({key:value})
    with pytest.raises(ValueError): checker.check("before")
    assert calls==[]


@pytest.mark.parametrize("suffix",["host=other-db&user=postgres&password=other-password",
    "%68ost=other-db","host=postgres&host=other-db"])
def test_combined_encoded_repeated_override_refused(inputs,monkeypatch,suffix):
    calls=[]
    monkeypatch.setattr(checker,"create_engine",lambda *a,**k:calls.append((a,k)))
    inputs["DATABASE_URL"]+="?"+suffix
    with pytest.raises(ValueError):checker.check("after")
    assert calls==[]


def test_authority_and_dialect_override_reproduction_without_connection():
    url=make_url(API+"?host=other-db&user=postgres&password=other-password")
    kwargs=PGDialect_psycopg().create_connect_args(url)[1]
    assert url.host=="postgres" and url.username=="anxious_api"
    assert kwargs["host"]=="other-db" and kwargs["user"]=="postgres" and kwargs["password"]=="other-password"


class Result:
    def __init__(self,value): self.value=value
    def one(self): return self.value
    def scalar_one(self): return self.value


class Connection:
    def __init__(self,user,identity):self.user,self.identity=user,identity
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def exec_driver_sql(self,*args):pass
    def rollback(self):pass
    def execute(self,sql):
        value=str(sql)
        if "current_database()" in value:return Result(self.identity or ("hosting",self.user,self.user))
        if "version_num" in value:return Result("0001_auth")
        return Result(True)


class Engine:
    def __init__(self,user,identity):self.user,self.identity=user,identity
    def connect(self):return Connection(self.user,self.identity)
    def dispose(self):pass


def engines(monkeypatch,identity=None,failed_role="anxious_api"):
    calls=[]
    def factory(url,**kwargs):
        actual=PGDialect_psycopg().create_connect_args(url)[1]
        calls.append(actual)
        assert not url.query and actual["host"]=="postgres" and actual["dbname"]=="hosting"
        return Engine(url.username,identity if url.username==failed_role else None)
    monkeypatch.setattr(checker,"create_engine",factory)
    monkeypatch.setattr(checker,"validate_v1",lambda *a:None)
    monkeypatch.setattr(checker,"validate_research",lambda *a:"0004_publication")
    return calls


@pytest.mark.parametrize("stage",["before","after"])
def test_normal_authority_reaches_expected_roles(inputs,monkeypatch,stage):
    calls=engines(monkeypatch)
    assert checker.check(stage)["status"]=="ok"
    assert [value["user"] for value in calls]==["postgres","anxious_api"]
    assert calls[1]["password"]==PASSWORD


@pytest.mark.parametrize("role",["postgres","anxious_api"])
@pytest.mark.parametrize("column",[0,1,2])
def test_actual_database_current_and_session_user_mismatch_refused(inputs,monkeypatch,role,column):
    identity=["hosting",role,role]
    identity[column]="mismatched"
    engines(monkeypatch,tuple(identity),role)
    with pytest.raises(ValueError): checker.check("after")
