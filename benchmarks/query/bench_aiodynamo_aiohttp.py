import asyncio

from aiohttp import ClientSession
from boto3.dynamodb.conditions import Key
from pyperf import Runner

from aiodynamo.client import Client
from aiodynamo.credentials import Credentials
from aiodynamo.http.aiohttp import AIOHTTP
from utils import TABLE_NAME, KEY_FIELD, KEY_VALUE, REGION_NAME


async def inner():
    async with ClientSession() as session:
        client = Client(AIOHTTP(session), Credentials.auto(), REGION_NAME)
        items = [
            item
            async for item in client.query(TABLE_NAME, Key(KEY_FIELD).eq(KEY_VALUE))
        ]


def query_aiodynamo_aiohttp():
    asyncio.run(inner())


Runner().bench_func("query", query_aiodynamo_aiohttp)
