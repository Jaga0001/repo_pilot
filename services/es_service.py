from elasticsearch import Elasticsearch
from config import ELASTIC_URL, ELASTIC_INDEX, ELASTIC_API_KEY


class ElasticsearchService:

    def __init__(self):
        self.client = Elasticsearch(ELASTIC_URL, api_key=ELASTIC_API_KEY)

    def fetch_logs(self, build_id: str):

        query = {
            "query": {
                "match": {
                    "build_id": build_id
                }
            }
        }

        response = self.client.search(
            index=ELASTIC_INDEX,
            body=query
        )

        logs = []

        for hit in response["hits"]["hits"]:
            logs.append(hit["_source"]["log"])

        return "\n".join(logs)
