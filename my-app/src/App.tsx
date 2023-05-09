import React, { useState } from "react";
import "./App.css";

interface QueryFormProps {
  onSubmit: (datasetId: string, query: string) => void;
}

export function App({ onSubmit }: QueryFormProps) {
  const [datasetId, setDatasetId] = useState("");
  const [query, setQuery] = useState("");
  const [queryResult, setQueryResult] = useState("");

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onSubmit(datasetId, query);

    const data = {
      datasetId: datasetId,
      query: query
    };

    try {
      //const response = await fetch('http://127.0.0.1:5000/api/download-dataset', {
      const response = await fetch('http://localhost:8002/api/v1/queryduck?object_name=' + datasetId + '&query=' + query, {
        method: 'GET',
        headers: {
          'Access-Control-Allow-Origin': 'http://localhost:3000',
          'Access-Control-Allow-Headers': 'x-requested-with',
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
          'Access-Control-Allow-Credentials': 'true',
          'sec-ch-ua': 'Chromium";v="112", "Google Chrome";v="112", "Not:A-Brand";v="99"',
          'sec-ch-ua-mobile': '?0',
          'sec-ch-ua-platform': 'macOS',
          'Sec-Fetch-Dest': 'empty',
          'Sec-Fetch-Mode': 'cors',
          'Sec-Fetch-Site': 'cross-site',
          'origin': 'http://localhost:3000',
        }
        // body: JSON.stringify(data)
      });

      if (!response.ok) {
        throw new Error(`Failed to download dataset. Response status code: ${response.status}`);
      }

      // parse the response as JSON
      const responseData = await response.json();

      // update the query result state variable
      setQueryResult(JSON.stringify(responseData));

    } catch (error) {
      console.error(error);
    }
  };

  return (
    <div className="container">
      <div className="header">
        <h1>AUCTUS Querying with DataSet ID and SQL Query</h1>
      </div>
      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label className="form-label">
            Dataset ID:
            <input
              type="text"
              className="form-control"
              value={datasetId}
              onChange={(e) => setDatasetId(e.target.value)}
            />
          </label>
        </div>
        <div className="form-group">
          <label className="form-label">
            SQL Query:
            <textarea
              className="form-control"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
        </div>
        <button type="submit" className="btn btn-primary">
          Submit the Dataset ID and SQL Query that you want to query.
        </button>
      </form>
      <div className="table-container">
        <div className="query-result">
          {queryResult}
        </div>
      </div>
    </div>
  );
}

/*
import React, { useState } from "react";
import "./App.css";

interface QueryFormProps {
  onSubmit: (datasetId: string, query: string) => void;
}

export function App({ onSubmit }: QueryFormProps) {
  const [datasetId, setDatasetId] = useState("");
  const [query, setQuery] = useState("");

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onSubmit(datasetId, query);
  
    const data = {
      datasetId: datasetId,
      query: query
    };
  
    try {
      //const response = await fetch('http://127.0.0.1:5000/api/download-dataset', {
        const response = await fetch('http://127.0.0.1:5000/api/query', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(data)
      });
  
      if (!response.ok) {
        throw new Error(`Failed to download dataset. Response status code: ${response.status}`);
      }
  
      // parse the response as JSON
      const responseData = await response.json();
  
      // log the response data to the console
      console.log(responseData);
    } catch (error) {
      console.error(error);
    }
  };
  
  return (
    <div className="container">
      <div className="header">
        <h1>AUCTUS Querying with DataSet ID and SQL Query</h1>
      </div>
      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label className="form-label">
            Dataset ID:
            <input
              type="text"
              className="form-control"
              value={datasetId}
              onChange={(e) => setDatasetId(e.target.value)}
            />
          </label>
        </div>
        <div className="form-group">
          <label className="form-label">
            SQL Query:
            <textarea
              className="form-control"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
        </div>
        <button type="submit" className="btn btn-primary">
          Submit
        </button>
      </form>
      <div className="table-container">
      </div>
    </div>
  );
}

*/