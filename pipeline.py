            if PipelineStage.MESSAGE not in self.config.skip_stages:
                if qualified_ids:
                    await self._run_stage_messages(qualified_ids, report)
                else:
                    logger.info("[Pipeline] No startups qualified for message drafting.")
                    report.stages[PipelineStage.MESSAGE] = StageResult(
                        stage=PipelineStage.MESSAGE,
                        success=True,
                        items_processed=0,
                        items_succeeded=0,
                        items_failed=0,
                        duration_seconds=0.0,
                    )
            else:
                logger.info("[Pipeline] Skipping Message drafting stage.")

            report.success = not self.cancel_event.is_set()

        except Exception as e:
            logger.error(f"[Pipeline] Fatal error during execution: {e}", exc_info=True)
            report.success = False

        finally:
            report.completed_at = datetime.now(timezone.utc)
            report.total_duration_seconds = (
                report.completed_at - report.started_at
            ).total_seconds()

            status = "completed" if report.success else ("cancelled" if self.cancel_event.is_set() else "failed")
            fit_stage = report.stages.get(PipelineStage.FIT)
            founder_stage = report.stages.get(PipelineStage.FOUNDER)
            message_stage = report.stages.get(PipelineStage.MESSAGE)
            stage_errors: List[str] = []
            for stage in report.stages.values():
                stage_errors.extend([f"{stage.stage.value}: {err}" for err in stage.errors])

            stats = {
                "discovered": report.total_startups_discovered,
                "founders": report.total_founders_extracted,
                "fit_evaluated": report.total_fit_evaluated,
                "fit_failed": fit_stage.items_failed if fit_stage else 0,
                "founder_failed": founder_stage.items_failed if founder_stage else 0,
                "message_failed": message_stage.items_failed if message_stage else 0,
                "qualified": report.total_qualified,
                "drafts": report.total_drafts_generated,
            }
            error_summary = "Run cancelled by operator" if self.cancel_event.is_set() else None
            if stage_errors:
                detail = " | ".join(stage_errors[:12])
                error_summary = f"{error_summary} | {detail}" if error_summary else detail
                if len(error_summary) > 4000:
                    error_summary = error_summary[:3997] + "..."

            try:
                self.storage.record_pipeline_run_complete(
                    session_id=self.session_id,
                    status=status,
                    duration_seconds=report.total_duration_seconds,
                    stats=stats,
                    error_summary=error_summary,
                )
            except Exception as e:
                logger.warning(f"Failed to record pipeline run completion: {e}")

            # Clean up ephemeral session state
            try:
                self.memory.clear_session(self.session_id)
            except Exception as e: