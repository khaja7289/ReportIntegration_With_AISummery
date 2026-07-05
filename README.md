# ReportIntegration_With_AISummery

This repository contains a performance testing and reporting workflow for the PetStore sample test suite. It combines JMeter-based load testing with a reusable GitLab CI framework for generating dashboards, AI summary insights, and email notifications.

## Project Summary

- Runs performance tests using JMeter scripts located in the PetStore test project.
- Uses a shared CI pipeline framework from the perf-framework folder.
- Generates a performance dashboard and summary report from JMeter results.
- Supports optional AI-generated insights and email reporting.
- Includes a GitLab CI template that can be reused for other performance test projects.

## Repository Structure

- perf-framework/: shared pipeline logic, report generation, and notification scripts
- perf-tests-petstore/: sample project-specific test assets, SLA config, and CI template

## Key Files

- perf-tests-petstore/gitlab-ci-template.yml: GitLab CI template for project-level configuration
- perf-framework/pipeline.yml: shared pipeline definition used by the CI template
- perf-tests-petstore/scripts/PetStoreHar.jmx: sample JMeter test script
- perf-tests-petstore/config/sla.json: SLA thresholds for performance checks

## Notes for Future Reference

- Update the GitLab include project path in the CI template to match the actual GitLab group/project name.
- Set required CI/CD variables such as SMTP credentials and AI API keys in GitLab before running the pipeline.
- Keep Grafana URL blank if you do not want to publish results to Grafana.
- Use the email address configured in the CI template for report notifications.
