from openviking_sdk import SyncHTTPClient


def test_ov_client_add_resource(overwrite=False):
    client = SyncHTTPClient(
        url="http://192.168.198.128:11933",
        api_key="ak-d962b560f65644e3a91b3bb6bf9e5467",
        account="cycloneclaw",
        user="test-01",
    )


    try:
        client.initialize()

        # Add a resource
        # result = client.add_resource(
        #     "./data/simple.md"
        # )
        result = client.add_resource(
            path="./data/simple.md",
            to="viking://resources/folder23/simple.md",
            overwrite=overwrite
        )
        root_uri = result["root_uri"]
        print('result', result)

        # Wait for processing
        client.wait_processed()

        # Search
        results = client.find("APA新范式", target_uri=root_uri)
        for r in results['resources']:
            print(f"  {r['uri']} (score: {r['score']:.4f})")
            path = client.overview(r['uri'])
            print(f"  {path}")
            path = client.read(r['uri'])
            print("*"*20)
            print(f": {path}")
            print("==" * 20)


    finally:
        client.close()


def test_ov_client_add_user_resource(overwrite:bool=False):
    """测试 add_user_resource：文件直接到用户空间。"""
    account = "cycloneclaw"
    user = "user-01"
    client = SyncHTTPClient(
        url="http://192.168.198.128:11933",
        api_key="ak-d962b560f65644e3a91b3bb6bf9e5467",
        account=account,
        user=user,
    )

    try:
        client.initialize()

        # 1. 上传到用户空间
        result = client.add_user_resource(
            path="./data/simple.md",
            to="viking://user/resources/folder5/simple.md",
            overwrite=overwrite,
            wait=True,
        )
        root_uri = result["root_uri"]
        print(f"add_user_resource root_uri: {root_uri}")
        assert root_uri.startswith(f"viking://user/{user}/resources/"), \
            f"Expected user path, got: {root_uri}"

        # 2. 确认可搜索
        client.wait_processed(timeout=120)
        # Try without target_uri first (broader search)
        results = client.find("APA新范式", target_uri=root_uri)
        resources = results.get("resources", [])
        print(f"Search results ({root_uri}): {len(resources)} items")
        for r in results.get('resources', []):
            print(f"  {r['uri']} (score: {r['score']:.4f})")
        assert len(resources) > 0, "File should be searchable after add_user_resource"

        for r in resources:
            print(f"  {r['uri']} (score: {r['score']:.4f})")

        print("✅ add_user_resource test passed")

    finally:
        client.close()


if __name__ == "__main__":
    test_ov_client_add_resource(overwrite=True)
    # test_ov_client_add_user_resource(overwrite=True)
