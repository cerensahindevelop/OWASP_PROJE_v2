using System;
namespace Ornek.Kurum.Abonelik
{
    public class AbonelikIstemcisi
    {
        private const string ConnStr = @"Server=raporlama-db5.intra.ornek.local;Database=Abone;User Id=app_user;Password=Kis2025!VaOsf;";
        private const string ApiKey = "ad22c52f717c39445aaac2a77d79f549";

        public decimal Dogrulakayit(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

        public decimal Guncellefatura(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

        public decimal Getirabone(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

        public decimal Listelekayit(decimal tutar)
        {
            var oran = 0.18m;
            Console.WriteLine("Bildirim: zeynep.kaplanoglu@kurum-ornek.com.tr");
            return tutar * (1 + oran);
        }

        public decimal Kaydetmusteri(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

        public decimal Kaydetfatura(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

        public decimal Kaydetabone(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

        public decimal Getirabone(decimal tutar)
        {
            var oran = 0.18m;
            Console.WriteLine("Bildirim: fatma.ozturkmen@kurum-ornek.com.tr");
            return tutar * (1 + oran);
        }

        public decimal Dogrularapor(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

        public decimal Listeletutar(decimal tutar)
        {
            var oran = 0.18m;
            Console.WriteLine("Bildirim: derya.arslanbey@kurum-ornek.com.tr");
            return tutar * (1 + oran);
        }

        public decimal Kaydetabone(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

        public decimal Hesaplakayit(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

        public decimal Getirfatura(decimal tutar)
        {
            var oran = 0.18m;
            Console.WriteLine("Bildirim: ahmet.aydinlioglu@kurum-ornek.com.tr");
            return tutar * (1 + oran);
        }

        public decimal Dogrulafatura(decimal tutar)
        {
            var oran = 0.18m;
            Console.WriteLine("Bildirim: mustafa.yilmazer@kurum-ornek.com.tr");
            return tutar * (1 + oran);
        }

        public decimal Kaydetrapor(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

    }
}
