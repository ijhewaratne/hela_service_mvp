"""Private evidence objects with application-level envelope encryption (Q38).

Each object gets a random data key; the file is AES-256-GCM encrypted with it (bound to the evidence id),
and the data key is wrapped by the KeyProvider-backed FieldCipher. The object store only ever holds
ciphertext, so security does not rest on a storage-side encryption feature. No public URLs exist.
"""
from __future__ import annotations

import os
from typing import Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..security.crypto import FieldCipher


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...


class S3ObjectStore:
    def __init__(self, *, endpoint: str, access_key: str, secret_key: str, bucket: str):
        self.bucket = bucket
        self._s3 = boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access_key,
                                aws_secret_access_key=secret_key, region_name='us-east-1',
                                config=Config(signature_version='s3v4', retries={'max_attempts': 2},
                                              connect_timeout=3, read_timeout=10))

    def ensure_bucket(self) -> None:
        try:
            self._s3.head_bucket(Bucket=self.bucket)
        except ClientError:
            self._s3.create_bucket(Bucket=self.bucket)

    def put(self, key: str, data: bytes) -> None:
        # IfNoneMatch prevents silently overwriting an existing evidence object.
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=data, IfNoneMatch='*',
                            ContentType='application/octet-stream')

    def get(self, key: str) -> bytes:
        return self._s3.get_object(Bucket=self.bucket, Key=key)['Body'].read()


class EvidenceSealer:
    def __init__(self, cipher: FieldCipher):
        self._cipher = cipher

    def seal(self, evidence_id: str, plaintext: bytes) -> tuple[bytes, bytes]:
        """Returns (ciphertext_for_store, wrapped_data_key_for_db)."""
        data_key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        ciphertext = nonce + AESGCM(data_key).encrypt(nonce, plaintext, evidence_id.encode())
        wrapped = self._cipher.encrypt(data_key.hex(), context='provider_evidence.data_key')
        return ciphertext, wrapped

    def open(self, evidence_id: str, ciphertext: bytes, wrapped_key: bytes) -> bytes:
        data_key = bytes.fromhex(self._cipher.decrypt(wrapped_key, context='provider_evidence.data_key'))
        return AESGCM(data_key).decrypt(ciphertext[:12], ciphertext[12:], evidence_id.encode())
