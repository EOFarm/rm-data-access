import os
import logging
import logging.config
import json
import time
from urllib.parse import urlparse

import aioredis
import jwt
import boto3
from botocore.exceptions import ClientError

from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


logger = logging.getLogger(__name__)

logging.config.dictConfig({
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {
            'format': '%(levelname)s: %(message)s',
        },
        'verbose': {
            'format': '[%(asctime)s][%(module)s] %(levelname)s: %(message)s',
        }
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        }
    },
    'loggers': {
        '': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        }
    }
})


register_queue = os.environ['REDIS_REGISTER_QUEUE_KEY']
progress_set = os.environ['REDIS_REGISTER_PROGRESS_KEY']
success_set = os.environ['REDIS_REGISTER_SUCCESS_KEY']
failure_set = os.environ['REDIS_REGISTER_FAILURE_KEY']
wait_time = float(os.environ.get('WAIT_TIME', '0.3'))
time_limit = int(os.environ.get('TIME_LIMIT', '300'))

app = FastAPI()


class Product(BaseModel):
    type: str
    url: str


@app.get("/", response_class=JSONResponse)
def read_root():
    item = {'message': 'status: ok'}

    return JSONResponse(status_code=200, content=item)


@app.post("/register/")
async def register(product: Product, request: Request):
    client = await aioredis.create_redis(
        (os.environ['REDIS_HOST'], int(os.environ.get('REDIS_PORT', '6379'))),
        encoding='utf-8')
    try:
        auth_header = request.headers['Authorization']
        if not auth_header.startswith('Bearer '):
            raise Exception("Invalid authorization scheme")

        token = auth_header[len('Bearer '):]
        encode_token = jwt.decode(token, verify=False, algorithms=['RS256'])
        prefix = encode_token['pct_claims']['aud']
        # TODO make something with thetoken

    except Exception as e:
        message = {"message": f"Failed to authorize: {e}"}
        return JSONResponse(status_code=401, content=message)

    # get the URL and extract the path from the S3 URL
    try:

        parsed_url = urlparse(product.url)
        url = parsed_url.netloc + parsed_url.path

        await client.lpush(register_queue, url)
        time_index = 0

        while True:
            time.sleep(wait_time)
            time_index += wait_time
            if time_index >= time_limit or url not in await client.lrange(
                register_queue, -100, 100
            ):
                break
        proccessing_count = time_index
        while True:

            if time_index <= proccessing_count and await client.sismember(
                progress_set, url
            ):
                logger.info("Product '%s' is being proccessed!" % url)
            time.sleep(wait_time)
            time_index += wait_time
            if time_index >= time_limit or not await client.sismember(
                progress_set, url
            ):
                break

        if time_index >= time_limit:
            logger.info("Timeout while registering '%s'" % url)
            message = {"message": f"Timeout while registering '{url}'"}
            return JSONResponse(status_code=400, content=message)

        if await client.sismember(success_set, url):
            logger.info("Item '%s' was successfully registered" % url)
            message = {"message": f"Item '{url}' was successfully registered"}
            return JSONResponse(status_code=200, content=message)

        elif await client.sismember(failure_set, url):
            logger.info("Failed to register product %s" % url)
            message = {"message": f"Failed to register product {url}"}
            return JSONResponse(status_code=400, content=message)

    except Exception as e:
        message = {"message": f"Registration failed: {e}"}
        return JSONResponse(status_code=401, content=message)