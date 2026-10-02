import React from 'react';
import SQLAgentStatus from './SQLAgentStatus';
import QueryInput from './QueryInput';
import EntityRecognitionResults from './EntityRecognitionResults';
import BusinessContext from './BusinessContext';
import SQLGeneration from './SQLGeneration';
import QueryResults from './QueryResults';
import OptimizationSuggestions from './OptimizationSuggestions';

const QueryPage = ({
  sqlAgentStatus,
  query,
  setQuery,
  executeQuery,
  isLoading,
  handleKeyPress,
  entityRecognition,
  businessContext,
  sqlValidation,
  generatedSql,
  copySqlToClipboard,
  queryExecution,
  optimizationSuggestions,
  results,
  insight
}) => {
  console.log('QueryPage - results prop:', results);
  console.log('QueryPage - queryExecution prop:', queryExecution);
  console.log('QueryPage - insight prop:', insight);
  return (
    <>
      {/* SQL Agent Status */}
      <SQLAgentStatus sqlAgentStatus={sqlAgentStatus} />

      {/* Query Input */}
      <QueryInput
        query={query}
        setQuery={setQuery}
        executeQuery={executeQuery}
        isLoading={isLoading}
        handleKeyPress={handleKeyPress}
      />

      {/* Entity Recognition Results */}
      <EntityRecognitionResults entityRecognition={entityRecognition} />

      {/* Business Context */}
      <BusinessContext businessContext={businessContext} />

      {/* SQL Generation */}
      <SQLGeneration
        sqlValidation={sqlValidation}
        generatedSql={generatedSql}
        copySqlToClipboard={copySqlToClipboard}
        query={query}
      />

      {/* Query Results */}
      <QueryResults queryExecution={queryExecution} results={results} />

      {/* Insight */}
      {insight && insight.success && (
        <div className="card mb-3 border-info">
          <div className="card-header bg-info bg-opacity-10">
            <h5 className="mb-0 text-info">
              <i className="bi bi-lightbulb me-2"></i>Insight
            </h5>
          </div>
          <div className="card-body">
            <p className="mb-2">{insight.insight}</p>
            <div className="text-muted small">
              <i className="bi bi-arrow-right-circle me-1"></i>
              <strong>Follow-up:</strong> {insight.follow_up_question}
            </div>
          </div>
        </div>
      )}

      {/* Optimization Suggestions */}
      <OptimizationSuggestions optimizationSuggestions={optimizationSuggestions} />
    </>
  );
};

export default QueryPage; 