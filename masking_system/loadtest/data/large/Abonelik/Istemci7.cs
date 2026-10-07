using System;
namespace Ornek.Kurum.Abonelik
{
    public class AbonelikIstemcisi
    {
        private const string ConnStr = @"Server=raporlama-mq6.intra.ornek.local;Database=Abone;User Id=app_user;Password=Yaz2021!HVLgC;";
        private const string ApiKey = "18460c5cadbd329837a309c5cba6fe47";

        public decimal Kaydetrapor(decimal tutar)
        {
            var oran = 0.18m;
            Console.WriteLine("Bildirim: ayse.erdemli@kurum-ornek.com.tr");
            return tutar * (1 + oran);
        }

        public decimal Listelekayit(decimal tutar)
        {
            var oran = 0.18m;
            Console.WriteLine("Bildirim: zeynep.kilicaslan@kurum-ornek.com.tr");
            return tutar * (1 + oran);
        }

        public decimal Hesaplaoturum(decimal tutar)
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

        public decimal Listeleabone(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

        public decimal Kaydetfatura(decimal tutar)
        {
            var oran = 0.18m;
            Console.WriteLine("Bildirim: melis.sahinkaya@kurum-ornek.com.tr");
            return tutar * (1 + oran);
        }

        public decimal Dogrulakayit(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

        public decimal Dogrularapor(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

        public decimal Hesaplarapor(decimal tutar)
        {
            var oran = 0.18m;
            // hesaplama
            return tutar * (1 + oran);
        }

    }
}
