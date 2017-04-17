import asyncio
import os
from functools import wraps
import itertools

import attr
import pytest
from aiobotocore import get_session

from aiodynamo import Connection, model, Keys, field, hash_key, range_key
from aiodynamo.exceptions import NotModified, NotFound
from aiodynamo.helpers import remove_empty_strings


async def cleanup(client):
    response = await client.list_tables()
    for table in response['TableNames']:
        await client.delete_table(TableName=table)


@pytest.fixture()
def dynamo_client():
    try:
        endpoint_url = os.environ['DYNAMODB_ENDPOINT_URL']
    except KeyError:
        raise pytest.skip('No endpoint url specified')
    client = get_session().create_client(
        'dynamodb',
        endpoint_url=endpoint_url,
        region_name='us-east-1',
        aws_access_key_id='local',
        aws_secret_access_key='local',
    )
    try:
        yield client
    finally:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(cleanup(client))
        client.close()


def runner(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        task = loop.create_task(func(*args, **kwargs))
        loop.run_until_complete(task)
    return wrapper


@runner
async def test_basic(dynamo_client):
    @model(keys=Keys.HashRange)
    class MyModel:
        r = range_key(str)
        h = hash_key(str)

    router = {
        MyModel: 'my-table'
    }

    db = Connection(router=router, client=dynamo_client)
    await db.create_table(MyModel, read_cap=5, write_cap=5)
    instance = MyModel(r='r', h='h')
    await db.save(instance)
    db_instance = await db.lookup(MyModel, h='h', r='r')
    assert instance == db_instance
    await db.delete(db_instance)
    with pytest.raises(NotFound):
        await db.lookup(MyModel, h='h', r='r')


@runner
async def test_update(dynamo_client):
    @model(keys=Keys.HashRange)
    class MyModel:
        r = range_key(str)
        h = hash_key(str)
        a = field()
        b = field()

    router = {
        MyModel: 'my-table'
    }

    db = Connection(router=router, client=dynamo_client)
    await db.create_table(MyModel, read_cap=5, write_cap=5)
    instance = MyModel(r='r', h='h', a='a', b='b')
    await db.save(instance)
    modified = instance.modify(a= 'not a')
    with pytest.raises(NotModified):
        await db.update(instance)
    await db.update(modified)
    db_instance = await db.lookup(MyModel, r='r', h='h')
    assert db_instance == MyModel(r='r', h='h', a='not a', b='b')


@runner
async def test_query(dynamo_client):
    @model(keys=Keys.HashRange)
    class MyModel:
        r = range_key(str)
        h = hash_key(str)
        x = field()
    router = {
        MyModel: 'my-table'
    }
    db = Connection(router=router, client=dynamo_client)
    await db.create_table(MyModel, read_cap=5, write_cap=5)
    instances = [
        MyModel(r=f'r{index}', h='h', x=index) for index in range(10)
    ]
    await asyncio.wait(map(db.save, instances))
    db_instances = [instance async for instance in db.query(MyModel, h='h')]
    assert instances == db_instances


@runner
async def test_fixed_hash(dynamo_client):
    @model(keys=Keys.HashRange)
    class MyModel:
        h = hash_key(str, constant='hashkey')
        r = range_key(str)
    router = {
        MyModel: 'my-table'
    }
    db = Connection(router=router, client=dynamo_client)
    await db.create_table(MyModel, read_cap=5, write_cap=5)
    instance = MyModel(r='test')
    assert instance.h == 'hashkey'
    await db.save(instance)
    db_instance = await db.lookup(MyModel, r='test')
    assert instance == db_instance
    assert db_instance.h == 'hashkey'


@runner
async def test_alias(dynamo_client):
    @model(keys=Keys.Hash)
    class MyModel:
        h = hash_key(str, alias='hash_key')
    router = {
        MyModel: 'my-table'
    }
    db = Connection(router=router, client=dynamo_client)
    await db.create_table(MyModel, read_cap=5, write_cap=5)
    response = await dynamo_client.describe_table(TableName='my-table')
    assert response['Table']['KeySchema'] == [{
        'AttributeName': 'hash_key',
        'KeyType': 'HASH'
    }]
    instance = MyModel(h='h')
    await db.save(instance)
    db_instance = await db.lookup(MyModel, h='h')
    assert instance == db_instance


@runner
async def test_alias_hr(dynamo_client):
    @model(keys=Keys.HashRange)
    class MyModel:
        h = hash_key(str, alias='hash_key')
        r = range_key(str, alias='range_key')
    router = {
        MyModel: 'my-table'
    }
    db = Connection(router=router, client=dynamo_client)
    await db.create_table(MyModel, read_cap=5, write_cap=5)
    response = await dynamo_client.describe_table(TableName='my-table')
    assert response['Table']['KeySchema'] == [{
        'AttributeName': 'hash_key',
        'KeyType': 'HASH'
    },{
        'AttributeName': 'range_key',
        'KeyType': 'RANGE'
    }]
    instance = MyModel(h='hv', r='rv')
    await db.save(instance)
    db_instance = await db.lookup(MyModel, h='hv', r='rv')
    assert instance == db_instance



@runner
async def test_non_field(dynamo_client):
    @model(keys=Keys.Hash)
    class MyModel:
        key = hash_key(str)
        attr = attr.ib(default='not-attr')

    router = {
        MyModel: 'my-table'
    }
    db = Connection(router=router, client=dynamo_client)
    await db.create_table(MyModel, read_cap=5, write_cap=5)

    instance = MyModel(key='mykey', attr='attr')
    await db.save(instance)
    db_instance = await db.lookup(MyModel, key='mykey')
    assert db_instance != instance
    assert db_instance.attr == 'not-attr'
    assert attr.assoc(db_instance, attr='attr') == instance


@runner
async def test_to_from_db_convert(dynamo_client):
    def prefixed(prefix: str):
        def to_db(suffix: str) -> str:
            return prefix + suffix

        def from_db(value: str) -> str:
            if value.startswith(prefix):
                return value[len(prefix):]
            else:
                return value

        to_db.from_db = from_db
        return to_db

    @model(keys=Keys.Hash)
    class MyModel:
        prefixed_key = hash_key(str, convert=prefixed('TEST'))

        @property
        def unprefixed(self):
            return self.prefixed_key[4:]

    router = {
        MyModel: 'my-model'
    }
    db = Connection(router=router, client=dynamo_client)
    await db.create_table(MyModel, read_cap=5, write_cap=5)

    instance = MyModel(prefixed_key='suffix')
    assert instance.prefixed_key == 'TESTsuffix'
    assert instance.unprefixed == 'suffix'
    await db.save(instance)
    response = await db.client.get_item(
        TableName='my-model',
        Key={'prefixed_key': {'S': 'TESTsuffix'}}
    )
    assert response['Item']['prefixed_key'] == {'S': 'TESTsuffix'}
    db_instance = await db.lookup(MyModel, prefixed_key='suffix')
    assert db_instance == instance

@runner
async def test_auto_field(dynamo_client):
    counter = itertools.count(start=1)

    @model(keys=Keys.Hash)
    class MyModel:
        key = hash_key(str)
        auto_field = field(auto=lambda: next(counter), default=0)

    router = {
        MyModel: 'my-model'
    }

    db = Connection(router=router, client=dynamo_client)
    await db.create_table(MyModel, read_cap=5, write_cap=5)

    instance = MyModel(key='test')
    assert instance.auto_field == 0
    await db.save(instance)
    db_instance = await db.lookup(MyModel, key='test')
    assert db_instance != instance
    assert db_instance.auto_field == 1


@runner
async def test_routing(dynamo_client):
    @model(keys=Keys.Hash)
    class A:
        key = hash_key(str)

    @model(keys=Keys.Hash)
    class B:
        key = hash_key(str)

    router = {
        A: 'table-a',
        B: 'table-b',
    }

    db = Connection(router=router, client=dynamo_client)
    await db.create_table(A, read_cap=5, write_cap=5)
    await db.create_table(B, read_cap=5, write_cap=5)

    a = A(key='test')
    b = B(key='test')

    assert a != b
    assert attr.asdict(a) == attr.asdict(b)

    await db.save(a)
    await db.save(b)

    db_a = await db.lookup(A, key='test')
    db_b = await db.lookup(B, key='test')

    assert db_a != db_b

    assert db_a == a
    assert db_b == b


@runner
@pytest.mark.parametrize('test_value', [
    '',
    [''],
    {'key': ''},
    {'key1': '', 'key2': 'key2'},
    {'', 'notempty'},
    {''},
    0,
    [0],
    [None],
    None
], ids=repr)
async def test_empty_strings(dynamo_client, test_value):
    @model(keys=Keys.Hash)
    class MyModel:
        key = hash_key(str)
        value = field(default=type(test_value))

    router = {
        MyModel: 'my-model',
    }
    db = Connection(router=router, client=dynamo_client)
    await db.create_table(MyModel, read_cap=5, write_cap=5)
    instance = MyModel(key='key', value=test_value)
    await db.save(instance)
    db_instance = await db.lookup(MyModel, key='key')
    assert db_instance == instance


@pytest.mark.parametrize('value, expected', [
    ('', ''),
    ([''], []),
    ({'key': ''}, {}),
    ({'key1': '', 'key2': 'key2'}, {'key2': 'key2'}),
    ({'', 'notempty'}, {'notempty'}),
    ({''}, set()),
    (0, 0),
    ([0], [0]),
    ([None], [None]),
    (None, None)
], ids=repr)
def test_remove_empty_strings(value, expected):
    assert remove_empty_strings(value) == expected