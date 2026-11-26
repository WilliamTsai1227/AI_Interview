"""招募系統 API → job_posts 欄位的轉換（不連網路、不碰資料庫）。

執行：cd data/job_crawlers && ../../backend/.venv/bin/python -m pytest -q tests
"""
import json

from crawler.ats import crawl_board, greenhouse, lever, ashby
from crawler.http import Response
from crawler.normalize import normalize

GH_SRC = {"name": "appier", "company_name": "Appier", "ats": "greenhouse", "board": "appier"}


def _gh_job(id_, loc, content="&lt;p&gt;Build &lt;b&gt;Python&lt;/b&gt; backend APIs on AWS&lt;/p&gt;"):
    return {"id": id_, "title": "Backend Engineer", "location": {"name": loc}, "offices": [],
            "absolute_url": f"https://job-boards.greenhouse.io/appier/jobs/{id_}", "content": content,
            "first_published": "2026-11-01T02:18:10-04:00", "updated_at": "2026-11-20T06:29:17-04:00"}


def test_greenhouse_keeps_only_taiwan_jobs():
    data = {"jobs": [_gh_job(1, "Taipei, Taiwan"), _gh_job(2, "Tokyo, Japan"), _gh_job(3, "Remote - Taiwan")]}
    out = greenhouse(GH_SRC, data)
    assert [p["identifier"] for p in out] == ["appier:1", "appier:3"]


def test_greenhouse_posting_normalizes():
    p = greenhouse(GH_SRC, {"jobs": [_gh_job(7, "Taipei, Taiwan")]})[0]
    n = normalize(p, p["url"])
    assert n["title"] == "Backend Engineer" and n["company_name"] == "Appier"
    assert n["city"] == "台北市" and n["review_status"] == "published"
    assert n["external_ref"] == "appier:7"
    assert "<" not in n["description"] and "Python" in n["tags"] and "AWS" in n["tags"]
    assert n["posted_at"].year == 2026


def test_lever_taiwan_salary_and_employment():
    job = {"id": "abc", "text": "後端工程師 Backend Engineer", "hostedUrl": "https://jobs.lever.co/pinkoi/abc",
           "country": "TW", "workplaceType": "hybrid", "createdAt": 1764000000000,
           "categories": {"commitment": "Full-time", "location": "Taipei", "allLocations": ["Taipei"]},
           "description": "<p>打造電商後端服務</p>", "lists": [{"text": "Requirements", "content": "<li>Go or Python</li>"}],
           "additional": "", "salaryRange": {"currency": "TWD", "min": 1200000, "max": 1800000, "interval": "per-year-salary"}}
    other = {**job, "id": "xyz", "country": "JP", "categories": {"location": "Tokyo", "allLocations": ["Tokyo"]}}
    out = lever({"name": "pinkoi", "company_name": "Pinkoi", "ats": "lever", "board": "pinkoi"}, [job, other])
    assert len(out) == 1
    n = normalize(out[0], out[0]["url"])
    assert n["employment_type"] == "全職"
    assert (n["salary_min"], n["salary_max"], n["salary_period"]) == (1200000, 1800000, "year")
    assert n["city"] == "台北市" and n["external_ref"] == "pinkoi:abc"
    assert "Go" in n["tags"] and "Python" in n["tags"]


def test_ashby_secondary_location_and_unlisted():
    base = {"title": "Data Engineer", "jobUrl": "https://jobs.ashbyhq.com/acme/1", "employmentType": "FullTime",
            "isRemote": False, "publishedAt": "2026-11-02T10:00:00.000+00:00", "descriptionHtml": "<p>Spark and Airflow</p>"}
    data = {"jobs": [
        {**base, "id": "1", "location": "Singapore", "secondaryLocations": [{"location": "Taipei, Taiwan"}]},
        {**base, "id": "2", "location": "Taipei", "isListed": False},
        {**base, "id": "3", "location": "Berlin"},
    ]}
    out = ashby({"name": "acme", "company_name": "Acme", "ats": "ashby", "board": "acme"}, data)
    assert [p["identifier"] for p in out] == ["acme:1"]
    n = normalize(out[0], out[0]["url"])
    assert n["employment_type"] == "全職" and n["city"] == "台北市"


class FakeFetcher:
    def __init__(self, status, body):
        self.status, self.body, self.urls = status, body, []

    def get_api(self, url):
        self.urls.append(url)
        return Response(url, self.status, self.body, "application/json")


def test_crawl_board_marks_source_and_foreign():
    f = FakeFetcher(200, json.dumps({"jobs": [_gh_job(9, "Taipei, Taiwan"), _gh_job(10, "London, UK")]}))
    jobs, issues, complete = crawl_board(f, {**GH_SRC, "foreign": True})
    assert f.urls == ["https://boards-api.greenhouse.io/v1/boards/appier/jobs?content=true"]
    assert complete and not issues
    assert [(j["source_site"], j["external_ref"], j["is_foreign"]) for j in jobs] == [("greenhouse", "appier:9", True)]


def test_crawl_board_failures_are_not_complete():
    # 讀不到完整職缺板時，complete=False：呼叫端不會據此下架任何職缺
    for status, body in [(404, ""), (500, "oops"), (200, "<html>not json</html>")]:
        jobs, issues, complete = crawl_board(FakeFetcher(status, body), GH_SRC)
        assert jobs == [] and issues and complete is False
    jobs, issues, complete = crawl_board(FakeFetcher(200, "{}"), {**GH_SRC, "ats": "workday"})
    assert complete is False and "不支援" in issues[0]


def test_crawl_board_no_taiwan_jobs_is_still_complete():
    # 職缺板讀到了、只是沒有台灣職缺：complete=True，之前上架的台灣職缺會被下架
    jobs, issues, complete = crawl_board(FakeFetcher(200, json.dumps({"jobs": [_gh_job(1, "Paris, France")]})), GH_SRC)
    assert jobs == [] and complete is True and issues == ["這家公司目前沒有台灣的職缺"]


def test_category_ignores_company_boilerplate():
    # 描述開頭是「AI 公司」的簡介：業務職缺不能被歸成機器學習；技術職缺用職稱或部門判斷
    intro = "&lt;p&gt;Appier is a SaaS company that uses AI and machine learning.&lt;/p&gt;"
    sales = {**_gh_job(1, "Taipei, Taiwan", intro), "title": "Account Manager", "departments": [{"name": "Account Management"}]}
    eng = {**_gh_job(2, "Taipei, Taiwan", intro), "title": "Software Engineer", "departments": [{"name": "Backend"}]}
    jobs, _, _ = crawl_board(FakeFetcher(200, json.dumps({"jobs": [sales, eng]})), GH_SRC)
    assert [(j["category_code"], j["review_status"]) for j in jobs] == [("other", "published"), ("backend", "published")]


def test_greenhouse_offices_only_when_all_in_taiwan():
    # Canonical：每個職缺都掛全球辦公室，地點寫北京的不能因為辦公室清單有台北就收
    world = [{"name": "Taipei", "location": "Taipei, Taiwan"}, {"name": "Beijing", "location": "Beijing, China"}]
    beijing = {**_gh_job(1, "Office Based - Beijing, China"), "offices": world}
    # Dcard：地點只寫公司名，辦公室只有台北
    dcard = {**_gh_job(2, "Dcard"), "offices": [{"name": "Taipei", "location": "Taipei, Taiwan"}]}
    out = greenhouse(GH_SRC, {"jobs": [beijing, dcard]})
    assert [p["identifier"] for p in out] == ["appier:2"]
    assert out[0]["jobLocation"][0]["address"]["addressLocality"] == "Taipei, Taiwan"
