"""
Модуль статистики обработки файлов
"""

import json
import os
from datetime import datetime
from typing import Dict, List


class ProcessingStats:
    """Класс для сбора и анализа статистики обработки файлов"""
    
    def __init__(self, stats_file: str = "processing_stats.json"):
        self.stats_file = stats_file
        self.stats: Dict = self._load_stats()
        
    def _load_stats(self) -> Dict:
        """Загрузка статистики из файла"""
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Ошибка загрузки статистики: {e}")
                return {"history": [], "summary": {}}
        return {"history": [], "summary": {}}
    
    def _save_stats(self):
        """Сохранение статистики в файл"""
        try:
            with open(self.stats_file, 'w', encoding='utf-8') as f:
                json.dump(self.stats, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Ошибка сохранения статистики: {e}")
    
    def add_processing_record(self, 
                            file_path: str,
                            file_size: int,
                            duration: float,
                            conversion_time: float = 0,
                            transcription_time: float = 0,
                            success: bool = True):
        """Добавление записи о обработке файла"""
        file_ext = os.path.splitext(file_path)[1].lower()
        
        record = {
            "timestamp": datetime.now().isoformat(),
            "file_name": os.path.basename(file_path),
            "file_size_mb": round(file_size / (1024 * 1024), 2),
            "file_extension": file_ext,
            "total_duration": round(duration, 2),
            "conversion_time": round(conversion_time, 2),
            "transcription_time": round(transcription_time, 2),
            "success": success
        }
        
        self.stats["history"].append(record)
        self._update_summary()
        self._save_stats()
    
    def _update_summary(self):
        """Обновление сводной статистики"""
        if not self.stats["history"]:
            return
        
        successful = [r for r in self.stats["history"] if r["success"]]
        
        if not successful:
            return
        
        # Группировка по расширениям
        by_extension = {}
        for record in successful:
            ext = record["file_extension"]
            if ext not in by_extension:
                by_extension[ext] = []
            by_extension[ext].append(record)
        
        # Расчёт средних значений
        summary = {}
        for ext, records in by_extension.items():
            avg_size = sum(r["file_size_mb"] for r in records) / len(records)
            avg_media_duration = sum(r["total_duration"] for r in records) / len(records)
            avg_conversion = sum(r["conversion_time"] for r in records) / len(records)
            avg_transcription = sum(r["transcription_time"] for r in records) / len(records)
            avg_total_time = avg_conversion + avg_transcription
            
            # Коэффициент обработки: секунды обработки на секунду аудио
            processing_ratio = avg_total_time / avg_media_duration if avg_media_duration > 0 else 1.0
            
            # Отдельные коэффициенты для конвертации и транскрибации
            conversion_ratio = avg_conversion / avg_media_duration if avg_media_duration > 0 else 0.05
            transcription_ratio = avg_transcription / avg_media_duration if avg_media_duration > 0 else 0.95
            
            summary[ext] = {
                "count": len(records),
                "avg_size_mb": round(avg_size, 2),
                "avg_media_duration_sec": round(avg_media_duration, 2),
                "avg_conversion_sec": round(avg_conversion, 2),
                "avg_transcription_sec": round(avg_transcription, 2),
                "processing_ratio": round(processing_ratio, 3),  # Секунды обработки / секунда аудио
                "conversion_ratio": round(conversion_ratio, 3),
                "transcription_ratio": round(transcription_ratio, 3)
            }
        
        self.stats["summary"] = summary
    
    def estimate_processing_time(self, file_path: str, media_duration: float) -> float:
        """
        Оценка времени обработки на основе длительности медиа
        
        Args:
            file_path: путь к файлу
            media_duration: длительность аудио/видео в секундах
            
        Returns:
            Оценка времени обработки в секундах
        """
        file_ext = os.path.splitext(file_path)[1].lower()
        
        # Если нет длительности, используем дефолтную оценку
        if media_duration <= 0:
            return 30.0  # Минимальная оценка
        
        # Если есть статистика по этому расширению
        if file_ext in self.stats.get("summary", {}):
            ext_stats = self.stats["summary"][file_ext]
            processing_ratio = ext_stats.get("processing_ratio", 1.0)
            
            # Оценка: длительность_медиа * коэффициент_обработки
            estimated = media_duration * processing_ratio
            return max(estimated, 5)  # Минимум 5 секунд
        
        # Если нет статистики по расширению, используем общую
        all_records = [r for r in self.stats.get("history", []) if r["success"] and r.get("total_duration", 0) > 0]
        if all_records:
            # Средний коэффициент обработки по всем файлам
            total_ratios = []
            for r in all_records:
                media_dur = r["total_duration"]
                processing_time = r["conversion_time"] + r["transcription_time"]
                if media_dur > 0:
                    total_ratios.append(processing_time / media_dur)
            
            if total_ratios:
                avg_ratio = sum(total_ratios) / len(total_ratios)
                estimated = media_duration * avg_ratio
                return max(estimated, 5)
        
        # Дефолтная оценка: 0.5x (на 1 минуту аудио = ~30 секунд обработки)
        # Это консервативная оценка, реальная скорость зависит от железа
        return max(media_duration * 0.5, 5)
    
    def estimate_batch_time(self, files: List[tuple]) -> Dict:
        """
        Оценка времени обработки пакета файлов
        
        Args:
            files: список кортежей (file_path, media_duration)
            
        Returns:
            Словарь с оценками времени для каждого файла и общее время
        """
        estimates = {}
        total_time = 0
        
        for file_path, media_duration in files:
            estimated_time = self.estimate_processing_time(file_path, media_duration)
            estimates[file_path] = estimated_time
            total_time += estimated_time
        
        return {
            "per_file": estimates,
            "total_seconds": total_time
        }
    
    def get_statistics_summary(self) -> str:
        """Получить текстовую сводку статистики"""
        if not self.stats.get("history"):
            return "Статистика отсутствует"
        
        total_files = len(self.stats["history"])
        successful = len([r for r in self.stats["history"] if r["success"]])
        
        summary_text = f"Всего обработано файлов: {total_files}\n"
        summary_text += f"Успешно: {successful}\n"
        summary_text += f"Неудачно: {total_files - successful}\n\n"
        
        if self.stats.get("summary"):
            summary_text += "По типам файлов:\n"
            for ext, data in self.stats["summary"].items():
                summary_text += f"  {ext}: {data['count']} файлов, "
                summary_text += f"коэффициент обработки: {data['processing_ratio']}x "
                summary_text += f"(~{int(data['avg_media_duration_sec'])}с медиа -> ~{int(data['avg_media_duration_sec'] * data['processing_ratio'])}с обработки)\n"
        
        return summary_text