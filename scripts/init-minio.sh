#!/bin/sh
set -eu

mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mc mb --ignore-existing "local/$MINIO_BUCKET"
mc anonymous set none "local/$MINIO_BUCKET"

cat > /tmp/document-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetBucketLocation"],
      "Resource": ["arn:aws:s3:::$MINIO_BUCKET"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": ["arn:aws:s3:::$MINIO_BUCKET"],
      "Condition": {"StringLike": {"s3:prefix": ["documents/*"]}}
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": ["arn:aws:s3:::$MINIO_BUCKET/documents/*"]
    }
  ]
}
EOF
mc admin user add local "$DOCUMENT_MINIO_USER" "$DOCUMENT_MINIO_PASSWORD"
mc admin policy create local document-rag /tmp/document-policy.json
mc admin policy attach local document-rag --user "$DOCUMENT_MINIO_USER"
